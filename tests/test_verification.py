"""承重断言核验测试（设计文档_R4 §3.1 / 执行计划 Task 15）。全 mock，不碰真模型/真网络。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import ToolMessage

from octop_harness.middleware.chapter_gate import ChapterGateMiddleware
from octop_harness.middleware.verification import (
    extract_ledger_assertions,
    extract_loadbearing_assertions,
    sample_numeric_claims,
    verify_assertion,
    with_retry,
)

LEDGER_PLAIN = """# 数据台账

| 指标 | 数值 | 单位 | 口径 | 来源 | 首现章节 |
| --- | --- | --- | --- | --- | --- |
| 营收 | 70.96 | 亿元 | 2024年报 | 年报 | 03 |
"""

LEDGER_LISTING = """# 数据台账

| 指标 | 数值 | 单位 | 口径 | 来源 | 首现章节 |
| --- | --- | --- | --- | --- | --- |
| 上市状态 | 上交所主板 | — | 2024年报 | 年报 | 03 |
"""

CLEAN_TEXT = "本章为定性描述，没有任何精确数值。\n"
CH = "03_剖析.md"


def make_ws(tmp_path: Path, chapters: dict[str, str], ledger: str = LEDGER_PLAIN) -> Path:
    ws = tmp_path / "ws"
    (ws / "chapters").mkdir(parents=True)
    for name, text in chapters.items():
        (ws / "chapters" / name).write_text(text, encoding="utf-8")
    (ws / "data_ledger.md").write_text(ledger, encoding="utf-8")
    return ws


class MockModel:
    """按 system 分派：抽取提示词 → extract_payload；判定提示词 → judge_payload。"""

    def __init__(
        self,
        extract_payload: list[str] | None = None,
        judge_payload: dict | None = None,
        error: Exception | None = None,
    ) -> None:
        self._extract = extract_payload or []
        self._judge = judge_payload or {"verdict": "confirmed", "reason": "正文支持"}
        self._error = error
        self.calls = 0

    async def ainvoke_text(self, prompt: str, **kwargs: object) -> str:
        self.calls += 1
        if self._error is not None:
            raise self._error
        if "抽取" in str(kwargs.get("system") or ""):
            return json.dumps({"assertions": self._extract}, ensure_ascii=False)
        return json.dumps(self._judge, ensure_ascii=False)


async def search_ok(query: str) -> list[dict]:
    return [{"url": "https://example.com/evidence", "title": "t", "snippet": "s"}]


async def fetch_ok(url: str) -> str:
    return "来源正文：欧普照明 2016 年于上交所主板上市。"


async def fetch_error(url: str) -> str:
    return "Error: web_fetch request failed"


def make_gate(
    ws: Path,
    model: MockModel | None,
    fetch=fetch_ok,
    search=search_ok,
) -> ChapterGateMiddleware:
    gate = ChapterGateMiddleware(ws, model_access=model, verify_search=search, verify_fetch=fetch)
    gate._verify_backoff = 0  # 测试不等退避
    return gate


def _request(name: str, args: dict) -> MagicMock:
    req = MagicMock()
    req.tool_call = {"name": name, "id": "call_1", "args": args}
    return req


async def _stats(mw: ChapterGateMiddleware, path: str) -> ToolMessage:
    async def handler(req):
        return ToolMessage(content="统计略", name="report_stats", tool_call_id="call_1")

    return await mw.awrap_tool_call(_request("report_stats", {"path": path}), handler)


async def _write(mw: ChapterGateMiddleware, path: str) -> ToolMessage:
    async def handler(req):
        return ToolMessage(content="ok", name="write_file", tool_call_id="call_1")

    return await mw.awrap_tool_call(_request("write_file", {"file_path": path}), handler)


# ---------------------------------------------------------------------------
# 抽取（三路并集）
# ---------------------------------------------------------------------------


async def test_ledger_reverse_lookup_forces_verification(tmp_path: Path) -> None:
    """②台账反查：台账含「上市状态」指标，正文无关键词命中 → 仍进核验清单。"""
    text = "本章全部是中性的行业描述，没有任何敏感词。\n"
    assertions = await extract_loadbearing_assertions(text, LEDGER_LISTING, model_access=None)
    assert any("上市状态" in a for a in assertions)


def test_ledger_assertion_skips_header_and_separator() -> None:
    assertions = extract_ledger_assertions(LEDGER_LISTING)
    assert assertions == ["上市状态：上交所主板—（2024年报）"]


async def test_model_extraction_merges_and_dedupes() -> None:
    """①+③并集去重：关键词句 + 模型抽取。"""
    text = "欧普照明已于上交所上市。本章营收 70.96 亿元。\n"
    model = MockModel(extract_payload=["欧普照明已于上交所上市。", "欧普市值 300 亿元"])
    assertions = await extract_loadbearing_assertions(text, LEDGER_PLAIN, model_access=model)
    assert assertions.count("欧普照明已于上交所上市。") == 1
    assert "欧普市值 300 亿元" in assertions


def test_sample_numeric_claims_excludes_covered() -> None:
    text = "营收 70.96 亿元。欧普已于上交所上市。纯定性句。\n"
    samples = sample_numeric_claims(text, exclude=["营收 70.96 亿元。"], k=3)
    assert samples == []  # 数值句已被覆盖；关键词句与无数值句不入抽检


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------


async def test_verify_refuted() -> None:
    model = MockModel(judge_payload={"verdict": "refuted", "reason": "正文写 2016 年"})
    verdict = await verify_assertion("欧普 2020 年上市", model, search_ok, fetch_ok)
    assert verdict.result == "refuted"
    assert verdict.evidence_url == "https://example.com/evidence"


async def test_verify_unverifiable_when_no_evidence() -> None:
    model = MockModel()
    verdict = await verify_assertion("某断言", model, search_ok, fetch_error)
    assert verdict.result == "unverifiable"  # 抓取全失败 ≠ infra，判查无实证
    assert model.calls == 0  # 没拿到正文就不消耗判定调用


async def test_with_retry_exhaustion_raises() -> None:
    calls = 0

    async def boom() -> None:
        nonlocal calls
        calls += 1
        raise TimeoutError("infra")

    with pytest.raises(TimeoutError):
        await with_retry(boom, retries=2, backoff_s=0)
    assert calls == 3  # 1 + 2 次重试


# ---------------------------------------------------------------------------
# 门禁集成（章级第五道）
# ---------------------------------------------------------------------------


async def test_refuted_marks_chapter_dirty(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, {CH: CLEAN_TEXT})
    model = MockModel(
        extract_payload=["欧普照明 2020 年上市"],
        judge_payload={"verdict": "refuted", "reason": "实为 2016 年"},
    )
    gate = make_gate(ws, model)
    out = await _stats(gate, f"chapters/{CH}")
    st = gate._chapters[CH]
    assert st.status == "dirty"
    assert st.verified == "failed"
    assert any(f["check"] == "verification" for f in st.verify_findings)
    assert "证伪" in out.content
    # 证伪消耗 N=2：开新章被阻断
    blocked = await _write(gate, "chapters/04_新章.md")
    assert blocked.status == "error"


async def test_unverifiable_requests_downgrade(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, {CH: CLEAN_TEXT})
    model = MockModel(
        extract_payload=["欧普市占率全球第一"],
        judge_payload={"verdict": "unverifiable", "reason": "查无实证"},
    )
    gate = make_gate(ws, model)
    out = await _stats(gate, f"chapters/{CH}")
    st = gate._chapters[CH]
    assert st.status == "clean"  # 查无实证不置脏
    assert st.verified == "passed"
    assert "待核实" in out.content
    # 不阻断开新章
    allowed = await _write(gate, "chapters/04_新章.md")
    assert allowed.status != "error"


async def test_infra_failure_chapter_fail_open(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, {CH: CLEAN_TEXT})
    model = MockModel(error=TimeoutError("endpoint down"))
    gate = make_gate(ws, model)
    out = await _stats(gate, f"chapters/{CH}")
    st = gate._chapters[CH]
    assert st.verified == "pending"
    assert st.status == "clean"  # infra 失败不置脏
    assert model.calls == 3  # 1 + 2 重试
    assert "不消耗修复额度" in out.content
    # fail-open：不阻塞开新章
    allowed = await _write(gate, "chapters/04_新章.md")
    assert allowed.status != "error"
    # 事故留痕
    log = (ws / "verification_log.jsonl").read_text(encoding="utf-8")
    assert "infra_failure" in log


async def test_infra_outage_aborts_round(tmp_path: Path) -> None:
    chapters = {f"0{i}_章{i}.md": CLEAN_TEXT for i in range(1, 5)}
    ws = make_ws(tmp_path, chapters)
    model = MockModel(error=TimeoutError("endpoint down"))
    gate = make_gate(ws, model)
    out = await _stats(gate, "chapters/")
    assert gate._verify_aborted is True
    assert model.calls == 9  # 3 章 × 3 次尝试；第 4 章跳过核验
    assert gate._chapters["04_章4.md"].verified == "unchecked"
    assert "事故" in out.content
    log = (ws / "verification_log.jsonl").read_text(encoding="utf-8")
    assert "verify_aborted" in log


async def test_model_access_none_skips_verification(tmp_path: Path) -> None:
    """工序关闭（缺省）：既有行为全兼容。"""
    ws = make_ws(tmp_path, {CH: CLEAN_TEXT})
    gate = ChapterGateMiddleware(ws)  # 无 model_access
    out = await _stats(gate, f"chapters/{CH}")
    assert "本章检查通过" in out.content
    assert gate._chapters[CH].verified == "unchecked"
    assert not (ws / "verification_log.jsonl").exists()


# ---------------------------------------------------------------------------
# R4 终审修复 3a：正常无结果 ≠ 检索故障
# ---------------------------------------------------------------------------


async def test_default_search_no_results_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """终审复现：searchfree 正常返回 No results found. 被 json.loads 当 infra 故障，
    触发重试/pending/事故计数。修复后映射为空清单（→ unverifiable 路径）。"""
    from octop_harness.middleware import chapter_gate

    class _FakeTool:
        async def ainvoke(self, args: dict) -> str:
            return "No results found."

    monkeypatch.setattr("octop_harness.builtin.tools.web_search.searchfree.searchfree_search", _FakeTool())
    assert await chapter_gate._default_search("某查询") == []


async def test_default_search_error_still_infra(monkeypatch: pytest.MonkeyPatch) -> None:
    """Error: 开头仍是检索故障（两类状态不混用）。"""
    from octop_harness.middleware import chapter_gate

    class _FakeTool:
        async def ainvoke(self, args: dict) -> str:
            return "Error: 上游 502"

    monkeypatch.setattr("octop_harness.builtin.tools.web_search.searchfree.searchfree_search", _FakeTool())
    with pytest.raises(RuntimeError):
        await chapter_gate._default_search("某查询")


# ---------------------------------------------------------------------------
# 断言抽取：重复循环防护（round-6 实战：DeepSeek 断言枚举 ×57 复制撞上限）
# ---------------------------------------------------------------------------


class _RawModel:
    """返回原始字符串的抽取模型桩（记录调用参数）。"""

    def __init__(self, raw: str) -> None:
        self._raw = raw
        self.kwargs: dict = {}

    async def ainvoke_text(self, prompt: str, **kwargs: object) -> str:
        self.kwargs.update(kwargs)
        return self._raw


async def test_extraction_salvages_repetitive_truncated_output() -> None:
    """模型陷重复循环+截断：挽救式解析去重救回真断言，不再按 infra 失败处理。"""
    uniq = [f"断言{i}：内容{i}" for i in range(3)]
    # 模拟事故现场：JSON 开头正常，同一批断言复制 N 遍，最后半截截断
    body = '{"assertions": [' + ",".join(f'"{a}"' for a in uniq * 20) + ', "半截'
    model = _RawModel(body)
    assertions = await extract_loadbearing_assertions("中性文本。", LEDGER_PLAIN, model_access=model)
    assert assertions.count("断言0：内容0") == 1
    assert sorted(a for a in assertions if a.startswith("断言")) == uniq


async def test_extraction_passes_max_tokens_cap() -> None:
    """抽取调用必须带 max_tokens 上限：重复循环最多烧封顶值，不再烧满窗口。"""
    model = _RawModel('{"assertions": []}')
    await extract_loadbearing_assertions("中性文本。", LEDGER_PLAIN, model_access=model)
    cap = model.kwargs.get("max_tokens")
    assert cap is not None and cap <= 4000


async def test_extraction_unsalvageable_still_raises() -> None:
    """输出完全无可用断言 → 仍抛错（不掩盖真故障）。"""
    model = _RawModel("我完全不知道怎么回答这个问题")
    with pytest.raises(ValueError, match=r"无 JSON"):
        await extract_loadbearing_assertions("中性文本。", LEDGER_PLAIN, model_access=model)


# ---------------------------------------------------------------------------
# round-7 实证修复：垃圾断言过滤 + 断言缓存键
# ---------------------------------------------------------------------------


def test_is_junk_assertion_filters_layout_fragments() -> None:
    from octop_harness.middleware.verification import is_junk_assertion

    assert is_junk_assertion("| 收购标的 | 对价 |")  # Markdown 表格行
    assert is_junk_assertion("│ 第三层 竞品层：10家拆解 │")  # 版式框线行
    assert is_junk_assertion("## 3.2 上市历程")  # 标题
    assert is_junk_assertion("   ")  # 空白
    assert not is_junk_assertion("欧普照明于 2020 年在上海证券交易所主板上市[1]。")


def test_assertion_key_normalizes_whitespace() -> None:
    from octop_harness.middleware.verification import assertion_key

    assert assertion_key("欧普照明 2020 年上市") == assertion_key("欧普照明  2020\n年上市")
    assert assertion_key("2020 年上市") != assertion_key("2021 年上市")


# ---------------------------------------------------------------------------
# round-8 实证修复（2026-11-06）：台账来源编号 = 查无实证的结案凭证
# ---------------------------------------------------------------------------

LEDGER_REGISTRY = """# 数据台账

## 一、引用编号登记（ref registry）

| ref | 来源条目 | 类型 | 数据期 |
| --- | --- | --- | --- |
| 1 | 欧普照明《2025年年度报告》，上海证券交易所 | A 法定披露 | 2025 年度 |
| 15 | 木林森股份有限公司《2025年年度报告》，巨潮资讯网 | A 法定披露 | 2025 年度 |

## 二、核心指标台账

| 指标名 | 数值 | 单位 | 口径/年份 | 来源 | 首现章节 | ref |
| --- | --- | --- | --- | --- | --- | --- |
| 中国照明产业规模 | 5,250 | 亿元 | 2025 年 | 中国照明电器协会 | 01 | 3 |
"""


def test_unverifiable_with_registered_ref_is_acknowledged() -> None:
    """断言自带已登记编号 → 凭台账结案，不进 verify_notes。"""
    from octop_harness.middleware.verification import is_acknowledged

    a = "木林森2025年营收174.39亿元，归母净利润-14.77亿元[15]"
    assert is_acknowledged(a, "无关正文。", LEDGER_REGISTRY)


def test_unverifiable_with_unregistered_ref_still_blocks() -> None:
    """编号未在台账登记（编号造假/漏登记）→ 不算凭证，继续卡。"""
    from octop_harness.middleware.verification import is_acknowledged

    a = "某企业 2025 年营收 999 亿元[42]。"
    assert not is_acknowledged(a, "无关正文。", LEDGER_REGISTRY)


def test_unverifiable_cleared_by_body_same_fact_ref() -> None:
    """抽取版断言无编号，但正文同事实句带已登记编号 → 出队（round-8 主形态）。"""
    from octop_harness.middleware.verification import is_acknowledged

    a = "木林森2025年营收174.39亿元，归母净利润-14.77亿元"
    body = "木林森 2025 年营收 174.39 亿元、同比 +3.13%、归母净利润 −14.77 亿元[15]。"
    assert is_acknowledged(a, body, LEDGER_REGISTRY)


def test_no_registry_keeps_old_behavior() -> None:
    """台账无登记表（或没传台账）→ 编号不算凭证，维持原判定。"""
    from octop_harness.middleware.verification import is_acknowledged

    a = "欧普照明于 2019 年完成对某照明企业的重大收购[1]。"
    assert not is_acknowledged(a, "无关正文。")
    assert not is_acknowledged(a, "无关正文。", LEDGER_PLAIN)


async def test_unverifiable_doubt_not_counted_as_note(tmp_path: Path) -> None:
    """round-8 实证：审校回流疑点是元评论（「请核对/需核实」），judge 判不动 →
    不得占「断言查无实证待处理」名额卡 merge，留痕 doubt_unresolved 即可。"""
    ws = make_ws(tmp_path, {CH: CLEAN_TEXT})
    doubt = "术语表说明「按拼音排序」，但条目顺序与拼音明显不符，请确认。"
    (ws / "verification_queue.jsonl").write_text(
        json.dumps({"chapter": CH, "doubt": doubt}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    model = MockModel(judge_payload={"verdict": "unverifiable", "reason": "元评论无法外部核验"})
    gate = make_gate(ws, model)
    await _stats(gate, f"chapters/{CH}")
    st = gate._chapters[CH]
    assert doubt not in st.verify_notes
    assert st.verified == "passed"
    log = (ws / "verification_log.jsonl").read_text(encoding="utf-8")
    assert "doubt_unresolved" in log
