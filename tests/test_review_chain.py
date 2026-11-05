"""审校链测试（设计文档_R4 §3.2 / 执行计划 Task 17）。全 mock。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from langchain_core.messages import ToolMessage

from octop_harness.middleware.chapter_gate import ChapterGateMiddleware
from octop_harness.middleware.review_chain import Edit, apply_edits, parse_review_response

LEDGER = """# 数据台账

| 指标 | 数值 | 单位 | 口径 | 来源 | 首现章节 |
| --- | --- | --- | --- | --- | --- |
| 已有指标 | 1 | 个 | 2024年报 | 年报 | 03 |
"""

# 非空壳长文（无精确数值、无承重关键词 → 内容检查器全静默），多行结构便于整行替换编辑。
# 注意：行尾用中文序号区分，不能含 ASCII 数字——否则被「一般数值抽检」抽样走核验通道。
_LINE = (
    "本章从行业格局、竞争壁垒、渠道结构、品牌心智与供应链效率等维度展开定性分析，"
    "着重讨论头部企业的相对优势与可持续性，不引用精确数值，不涉及重大资本运作。"
)
BODY = "\n".join(f"{_LINE}（第{n}段）" for n in "一二三四五六七八")

CH = "03_剖析.md"


def make_ws(tmp_path: Path, chapters: dict[str, str]) -> Path:
    ws = tmp_path / "ws"
    (ws / "chapters").mkdir(parents=True)
    for name, text in chapters.items():
        (ws / "chapters" / name).write_text(text, encoding="utf-8")
    (ws / "data_ledger.md").write_text(LEDGER, encoding="utf-8")
    return ws


class ReviewModel:
    """审校/核验双角色桩：system 含「审校」→ review_payload；否则核验抽取返回空。"""

    def __init__(self, review_payload: dict | None = None) -> None:
        self._review = review_payload or {"edits": [], "doubts": []}
        self.review_calls = 0

    async def ainvoke_text(self, prompt: str, **kwargs: object) -> str:
        if "审校" in str(kwargs.get("system") or ""):
            self.review_calls += 1
            return json.dumps(self._review, ensure_ascii=False)
        return '{"assertions": []}'


def _request(name: str, args: dict) -> MagicMock:
    req = MagicMock()
    req.tool_call = {"name": name, "id": "call_1", "args": args}
    return req


async def _call(mw: ChapterGateMiddleware, tool: str, args: dict) -> ToolMessage:
    async def handler(req):
        return ToolMessage(content="ok", name=tool, tool_call_id="call_1")

    return await mw.awrap_tool_call(_request(tool, args), handler)


# ---------------------------------------------------------------------------
# apply_edits 纯函数
# ---------------------------------------------------------------------------


def test_apply_edits_reverse_order_no_drift() -> None:
    text = "\n".join(f"第{i}行" for i in range(1, 13))
    edits = [Edit(line=3, new="改三"), Edit(line=10, new="改十")]
    new_text, applied, skipped = apply_edits(text, edits)
    lines = new_text.split("\n")
    assert lines[2] == "改三"  # 不倒序的话改完行 3 后行 10 会漂移
    assert lines[9] == "改十"
    assert len(applied) == 2 and not skipped
    assert applied[0]["line"] == 3  # audit 按行号正序


def test_out_of_range_line_skipped() -> None:
    text = "只有一行"
    new_text, applied, skipped = apply_edits(text, [Edit(line=9999, new="x")])
    assert new_text == text  # 不崩、不改
    assert not applied and len(skipped) == 1
    assert "越界" in skipped[0]["reason"]


def test_parse_review_response_filters_invalid() -> None:
    raw = (
        '```json\n{"edits": [{"line": 2, "new": "好", "reason": "r"}, {"line": 0, "new": "坏"}, '
        '{"line": 3, "new": "' + "长" * 301 + '"}], "doubts": ["营收口径存疑"]}\n```'
    )
    edits, doubts = parse_review_response(raw)
    assert [e.line for e in edits] == [2]  # 行 0 与超长编辑被过滤
    assert doubts == ["营收口径存疑"]


# ---------------------------------------------------------------------------
# merge 闸集成
# ---------------------------------------------------------------------------


async def test_merge_gate_auto_reviews_and_blocks(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, {CH: BODY})
    model = ReviewModel(
        {
            "edits": [{"line": 1, "new": "审校改写后的首段，保持定性表述，不含任何精确数值。", "reason": "精简"}],
            "doubts": [],
        }
    )
    mw = ChapterGateMiddleware(ws, model_access=model)
    mw._verify_backoff = 0
    await _call(mw, "report_stats", {"path": f"chapters/{CH}"})  # verified=passed
    first = await _call(mw, "merge_chapters", {})
    assert first.status == "error"
    assert "审校已改 1 处" in first.content
    st = mw._chapters[CH]
    assert st.reviewed is True
    assert (ws / "chapters" / CH).read_text(encoding="utf-8").startswith("审校改写后的首段")
    audit = (ws / "review_audit.jsonl").read_text(encoding="utf-8")
    assert '"line": 1' in audit and "精简" in audit
    # 复测后第二次 merge 放行
    second = await _call(mw, "merge_chapters", {})
    assert second.status != "error"


async def test_fact_doubt_flows_back_to_verification_queue(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, {CH: BODY})
    model = ReviewModel({"edits": [], "doubts": ["欧普市占率口径存疑"]})
    mw = ChapterGateMiddleware(ws, model_access=model)
    mw._verify_backoff = 0
    await _call(mw, "report_stats", {"path": f"chapters/{CH}"})
    await _call(mw, "merge_chapters", {})  # 无编辑 → 置 reviewed 放行；疑点入队
    queue = (ws / "verification_queue.jsonl").read_text(encoding="utf-8")
    assert "欧普市占率口径存疑" in queue

    # 下轮核验消费疑点：search 应收到疑点查询
    seen_queries: list[str] = []

    async def spy_search(q: str) -> list[dict]:
        seen_queries.append(q)
        return []

    mw2 = ChapterGateMiddleware(ws, model_access=model, verify_search=spy_search)
    mw2._verify_backoff = 0
    await _call(mw2, "report_stats", {"path": f"chapters/{CH}"})
    assert "欧普市占率口径存疑" in seen_queries
    # 核验成功后疑点出队
    assert "欧普市占率口径存疑" not in (ws / "verification_queue.jsonl").read_text(encoding="utf-8")
