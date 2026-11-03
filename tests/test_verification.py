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
) -> ChapterGateMiddleware:
    gate = ChapterGateMiddleware(ws, model_access=model, verify_fetch=fetch)
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
