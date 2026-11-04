"""ChapterGateMiddleware 状态机测试（设计文档_R3 §4.2 全分支 + 三个实现钉）。

分支覆盖：
- report_stats 评估 → clean/dirty 附言注入；
- 开新章拦截（write_file / edit_file 两个写工具面）；
- N=2 放行（logger.warning + 放行附言 + 额度复测复位）；
- 修改脏章置 unevaluated（修复路径永远不拦）；
- 非章节文件永远不拦；
- 骨架 write + edit 填充 = 同一次开章（实现钉 1）；
- merge 终闸阻断/放行。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import ToolMessage

from octop_harness.middleware.chapter_gate import ChapterGateMiddleware

LEDGER = """# 数据台账

| 指标 | 数值 | 单位 | 口径 | 来源 | 首现章节 |
| --- | --- | --- | --- | --- | --- |
| 已有指标 | 1 | 个 | 2024年报 | 年报 | 03 |
"""

DIRTY_CH = "06_产品线对比.md"
CLEAN_CH = "03_剖析.md"
NEW_CH = "07_渠道.md"

DIRTY_TEXT = "本章营收 25,000 万元，同比增长 8.96%。\n"
CLEAN_TEXT = "本章为定性描述，没有任何精确数值。\n"


def make_ws(
    tmp_path: Path,
    chapters: dict[str, str],
    ledger: str | None = LEDGER,
) -> Path:
    ws = tmp_path / "ws"
    (ws / "chapters").mkdir(parents=True)
    for name, text in chapters.items():
        (ws / "chapters" / name).write_text(text, encoding="utf-8")
    if ledger is not None:
        (ws / "data_ledger.md").write_text(ledger, encoding="utf-8")
    return ws


def _request(name: str, args: dict) -> MagicMock:
    req = MagicMock()
    req.tool_call = {"name": name, "id": "call_1", "args": args}
    return req


def _handler(calls: list[str] | None = None, content: str = "ok"):
    async def run(req) -> ToolMessage:
        if calls is not None:
            calls.append(req.tool_call["name"])
        return ToolMessage(
            content=content,
            name=req.tool_call["name"],
            tool_call_id="call_1",
        )

    return run


async def _stats(mw: ChapterGateMiddleware, chapter_path: str) -> ToolMessage:
    return await mw.awrap_tool_call(
        _request("report_stats", {"path": chapter_path}), _handler(content="统计略")
    )


async def _write(
    mw: ChapterGateMiddleware,
    tool: str,
    path: str,
    calls: list[str] | None = None,
) -> ToolMessage:
    return await mw.awrap_tool_call(
        _request(tool, {"file_path": path}), _handler(calls)
    )


# ---------------------------------------------------------------------------
# report_stats 触点：评估 + 附言
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stats_clean_chapter_pass_postscript(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, {CLEAN_CH: CLEAN_TEXT})
    mw = ChapterGateMiddleware(ws)
    out = await _stats(mw, f"chapters/{CLEAN_CH}")
    assert "[chapter_gate] 本章检查通过" in out.content
    assert mw._chapters[CLEAN_CH].status == "clean"


@pytest.mark.asyncio
async def test_stats_dirty_chapter_findings_postscript(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, {DIRTY_CH: DIRTY_TEXT})
    mw = ChapterGateMiddleware(ws)
    out = await _stats(mw, f"chapters/{DIRTY_CH}")
    assert "[chapter_gate] 本章检查发现" in out.content
    assert "修复后请再次调用 report_stats 复测" in out.content
    st = mw._chapters[DIRTY_CH]
    assert st.status == "dirty"
    assert any(f["check"] == "unsourced_precise" for f in st.findings)


@pytest.mark.asyncio
async def test_stats_directory_evaluates_all_chapters(tmp_path: Path) -> None:
    """模型实测习惯是目录统计（report_stats chapters/）——评估全部章并给合并附言。"""
    ws = make_ws(tmp_path, {DIRTY_CH: DIRTY_TEXT, CLEAN_CH: CLEAN_TEXT})
    mw = ChapterGateMiddleware(ws)
    out = await _stats(mw, "chapters")
    assert "[chapter_gate] 本次检查 2 章：1 章通过，1 章有问题" in out.content
    assert DIRTY_CH in out.content
    assert mw._chapters[DIRTY_CH].status == "dirty"
    assert mw._chapters[CLEAN_CH].status == "clean"
    # 目录统计标脏后，开新章同样被拦
    calls: list[str] = []
    blocked = await _write(mw, "write_file", f"chapters/{NEW_CH}", calls)
    assert blocked.status == "error"
    assert calls == []


@pytest.mark.asyncio
async def test_stats_directory_all_clean(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, {CLEAN_CH: CLEAN_TEXT})
    mw = ChapterGateMiddleware(ws)
    out = await _stats(mw, "chapters")
    assert "1 章通过，0 章有问题" in out.content
    assert "可继续下一章" in out.content


# ---------------------------------------------------------------------------
# 开新章拦截（write_file / edit_file 双面）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_new_chapter_blocked_when_dirty(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, {DIRTY_CH: DIRTY_TEXT})
    mw = ChapterGateMiddleware(ws)
    await _stats(mw, f"chapters/{DIRTY_CH}")
    calls: list[str] = []
    out = await _write(mw, "write_file", f"chapters/{NEW_CH}", calls)
    assert out.status == "error"
    assert "[chapter_gate] 门禁" in out.content
    assert calls == []  # handler 未被调用
    assert mw._chapters[DIRTY_CH].repair_rounds == 1


@pytest.mark.asyncio
async def test_open_new_chapter_blocked_via_edit_file(tmp_path: Path) -> None:
    """实现钉 1：edit_file 与 write_file 同一拦截面。"""
    ws = make_ws(tmp_path, {DIRTY_CH: DIRTY_TEXT})
    mw = ChapterGateMiddleware(ws)
    await _stats(mw, f"chapters/{DIRTY_CH}")
    calls: list[str] = []
    out = await _write(mw, "edit_file", f"chapters/{NEW_CH}", calls)
    assert out.status == "error"
    assert calls == []
    assert mw._chapters[DIRTY_CH].repair_rounds == 1


@pytest.mark.asyncio
async def test_repair_limit_releases_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = make_ws(tmp_path, {DIRTY_CH: DIRTY_TEXT})
    mw = ChapterGateMiddleware(ws)
    await _stats(mw, f"chapters/{DIRTY_CH}")
    await _write(mw, "write_file", f"chapters/{NEW_CH}")
    await _write(mw, "write_file", f"chapters/{NEW_CH}")
    assert mw._chapters[DIRTY_CH].repair_rounds == 2
    calls: list[str] = []
    with caplog.at_level(logging.WARNING, logger="octop_harness.middleware.chapter_gate"):
        out = await _write(mw, "write_file", f"chapters/{NEW_CH}", calls)
    assert out.status != "error"
    assert calls == ["write_file"]  # 放行
    assert "已达自修上限" in out.content  # 放行附言
    assert any("已达自修上限" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# 状态机闭环：修改脏章置 unevaluated → 复测刷新 → 放行
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_modify_dirty_chapter_allowed_and_marks_unevaluated(
    tmp_path: Path,
) -> None:
    ws = make_ws(tmp_path, {DIRTY_CH: DIRTY_TEXT})
    mw = ChapterGateMiddleware(ws)
    await _stats(mw, f"chapters/{DIRTY_CH}")
    calls: list[str] = []
    out = await _write(mw, "edit_file", f"chapters/{DIRTY_CH}", calls)
    assert out.status != "error"
    assert calls == ["edit_file"]  # 修脏章永远不拦
    assert mw._chapters[DIRTY_CH].status == "unevaluated"


@pytest.mark.asyncio
async def test_full_repair_loop_closes(tmp_path: Path) -> None:
    """dirty → 阻断 → 修脏章 → 复测 clean（额度复位）→ 开新章放行。"""
    ws = make_ws(tmp_path, {DIRTY_CH: DIRTY_TEXT})
    mw = ChapterGateMiddleware(ws)
    await _stats(mw, f"chapters/{DIRTY_CH}")
    blocked = await _write(mw, "write_file", f"chapters/{NEW_CH}")
    assert blocked.status == "error"
    await _write(mw, "edit_file", f"chapters/{DIRTY_CH}")
    # 模拟修复落盘：内容换成干净文本
    (ws / "chapters" / DIRTY_CH).write_text(CLEAN_TEXT, encoding="utf-8")
    out = await _stats(mw, f"chapters/{DIRTY_CH}")
    assert "本章检查通过" in out.content
    st = mw._chapters[DIRTY_CH]
    assert st.status == "clean"
    assert st.repair_rounds == 0  # 复测通过额度复位
    calls = []
    out = await _write(mw, "write_file", f"chapters/{NEW_CH}", calls)
    assert out.status != "error"
    assert calls == ["write_file"]


# ---------------------------------------------------------------------------
# 不拦的面：非章节文件、已存在章节、骨架+填充同窗口
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_chapter_files_never_blocked(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, {DIRTY_CH: DIRTY_TEXT})
    mw = ChapterGateMiddleware(ws)
    await _stats(mw, f"chapters/{DIRTY_CH}")
    for path in ("progress.md", "data_ledger.md", "outline.md", "notes/todo.md"):
        calls: list[str] = []
        out = await _write(mw, "write_file", path, calls)
        assert out.status != "error", path
        assert calls == ["write_file"], path


@pytest.mark.asyncio
async def test_existing_chapter_write_allowed_even_with_dirty_other(
    tmp_path: Path,
) -> None:
    ws = make_ws(tmp_path, {DIRTY_CH: DIRTY_TEXT, CLEAN_CH: CLEAN_TEXT})
    mw = ChapterGateMiddleware(ws)
    await _stats(mw, f"chapters/{DIRTY_CH}")
    calls: list[str] = []
    out = await _write(mw, "edit_file", f"chapters/{CLEAN_CH}", calls)
    assert out.status != "error"
    assert calls == ["edit_file"]


@pytest.mark.asyncio
async def test_skeleton_write_then_edit_fill_same_opening(tmp_path: Path) -> None:
    """实现钉 1：write 骨架 + edit 填充 = 同一次开章，填充不被当作新开章拦。"""
    ws = make_ws(tmp_path, {DIRTY_CH: DIRTY_TEXT})
    mw = ChapterGateMiddleware(ws)
    # 无脏章时先开新章写骨架（放行）
    calls: list[str] = []
    await _write(mw, "write_file", f"chapters/{NEW_CH}", calls)
    assert calls == ["write_file"]
    # 骨架落盘（真实 write 会创建文件；这里模拟）
    (ws / "chapters" / NEW_CH).write_text("# 骨架\n", encoding="utf-8")
    # 之后另一章变脏
    await _stats(mw, f"chapters/{DIRTY_CH}")
    # edit 填充不算新开章 → 不拦
    calls = []
    out = await _write(mw, "edit_file", f"chapters/{NEW_CH}", calls)
    assert out.status != "error"
    assert calls == ["edit_file"]


# ---------------------------------------------------------------------------
# merge 终闸
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_gate_blocks_then_releases(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = make_ws(tmp_path, {DIRTY_CH: DIRTY_TEXT})
    mw = ChapterGateMiddleware(ws)
    calls: list[str] = []
    out1 = await mw.awrap_tool_call(
        _request("merge_chapters", {}), _handler(calls)
    )
    assert out1.status == "error"
    assert "[chapter_gate] 合并闸门" in out1.content
    out2 = await mw.awrap_tool_call(
        _request("merge_chapters", {}), _handler(calls)
    )
    assert out2.status == "error"
    assert calls == []  # 两次都未真正执行
    with caplog.at_level(logging.WARNING, logger="octop_harness.middleware.chapter_gate"):
        out3 = await mw.awrap_tool_call(
            _request("merge_chapters", {}), _handler(calls)
        )
    assert out3.status != "error"
    assert calls == ["merge_chapters"]
    assert "已达自修上限" in out3.content
    assert any("已达自修上限" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_merge_gate_passes_clean_workspace(tmp_path: Path) -> None:
    # structure 检查器有空壳章线（<500 非空白字符），干净章需足量定性文字
    words = ["渠道", "品牌", "研发", "供应链", "组织", "服务", "营销", "产品"]
    body = "\n".join(
        f"本段从{a}{b}角度进行定性分析，不涉及任何量化数据。"
        for a in words
        for b in words
        if a != b
    )
    ws = make_ws(tmp_path, {CLEAN_CH: body})
    mw = ChapterGateMiddleware(ws)
    calls: list[str] = []
    out = await mw.awrap_tool_call(
        _request("merge_chapters", {}), _handler(calls)
    )
    assert out.status != "error"
    assert calls == ["merge_chapters"]


# ---------------------------------------------------------------------------
# 无关工具完全不触门禁
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unrelated_tool_passthrough(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, {DIRTY_CH: DIRTY_TEXT})
    mw = ChapterGateMiddleware(ws)
    await _stats(mw, f"chapters/{DIRTY_CH}")
    calls: list[str] = []
    out = await mw.awrap_tool_call(
        _request("read_file", {"file_path": f"chapters/{NEW_CH}"}), _handler(calls)
    )
    assert out.status != "error"
    assert calls == ["read_file"]


def test_config_flag_defaults_off() -> None:
    """G2：chapter_gate flag 默认关 = 零行为变化（不挂中间件）。"""
    from octop_harness.config import HarnessAgentConfig

    cfg = HarnessAgentConfig()
    assert cfg.chapter_gate_enabled is False
    assert cfg.chapter_gate_max_repairs == 2
    assert cfg.chapter_gate_workspace_root is None


# ---------------------------------------------------------------------------
# R4 Task 14：核验/审校独立模型通路注入（设计_R4 §3 裁定 15）
# ---------------------------------------------------------------------------


class _StubModelAccess:
    """ModelAccess 测试桩：只承诺 invoke_text/ainvoke_text 协议存在。"""

    def invoke_text(self, prompt: str, **kwargs: object) -> str:
        return ""

    async def ainvoke_text(self, prompt: str, **kwargs: object) -> str:
        return ""


def test_gate_accepts_model_access_injection(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, chapters={CLEAN_CH: CLEAN_TEXT})
    stub = _StubModelAccess()
    gate = ChapterGateMiddleware(ws, model_access=stub)
    assert gate.model_access is stub


def test_gate_default_model_access_none(tmp_path: Path) -> None:
    # 缺省 None = 核验/审校工序关闭，现有行为全兼容
    ws = make_ws(tmp_path, chapters={CLEAN_CH: CLEAN_TEXT})
    gate = ChapterGateMiddleware(ws)
    assert gate.model_access is None


# ---------------------------------------------------------------------------
# R4 Task 16：状态机扩展（verified/reviewed/exit_granted + 落盘恢复 + merge 闸）
# ---------------------------------------------------------------------------


class _MockVerifyModel:
    """核验模型桩：抽取返回空清单（章无承重断言 → verified=passed）。"""

    async def ainvoke_text(self, prompt: str, **kwargs: object) -> str:
        return '{"assertions": []}'


# 过 merge 闸 structure 检查的非空壳长文（无精确数值、无承重关键词 → 各检查器全静默）
CLEAN_LONG = (
    "本章从行业格局、竞争壁垒、渠道结构、品牌心智与供应链效率等维度展开定性分析，"
    "着重讨论头部企业在存量市场中的相对优势及其可持续性，不引用任何精确数值，"
    "也不涉及重大资本运作等承重事实，仅作框架性研判与趋势推演。"
) * 10


async def _merge_call(mw: ChapterGateMiddleware) -> ToolMessage:
    return await mw.awrap_tool_call(_request("merge_chapters", {}), _handler())


async def test_edit_clears_verified_and_reviewed(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, {CLEAN_CH: CLEAN_TEXT})
    mw = ChapterGateMiddleware(ws, model_access=_MockVerifyModel())
    await _stats(mw, f"chapters/{CLEAN_CH}")
    st = mw._chapters[CLEAN_CH]
    assert st.verified == "passed"
    st.reviewed = True  # 模拟审校链置位
    out = await _write(mw, "edit_file", f"chapters/{CLEAN_CH}")
    assert out.status != "error"  # 修改已存在章永远放行
    assert st.verified == "unchecked"  # 编辑即清（设计裁定 14）
    assert st.reviewed is False


async def test_review_chain_own_edit_exempt(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, {CLEAN_CH: CLEAN_TEXT})
    mw = ChapterGateMiddleware(ws, model_access=_MockVerifyModel())
    await _stats(mw, f"chapters/{CLEAN_CH}")
    mw.apply_review_edit(CLEAN_CH, "审校改写后的文字。\n")
    st = mw._chapters[CLEAN_CH]
    assert st.reviewed is True  # 审校自身改动豁免「编辑即清」的 reviewed 维度
    assert st.verified == "unchecked"  # 但文本已变 → 核验结论作废，merge 前补核重验（终审修复）
    assert st.verify_findings == [] and st.verify_notes == []
    assert st.status == "unevaluated"  # 但仍待复测
    assert (ws / "chapters" / CLEAN_CH).read_text(encoding="utf-8") == "审校改写后的文字。\n"


async def test_state_persisted_and_restored(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, {CLEAN_CH: CLEAN_TEXT})
    mw = ChapterGateMiddleware(ws, model_access=_MockVerifyModel())
    await _stats(mw, f"chapters/{CLEAN_CH}")
    mw.apply_review_edit(CLEAN_CH, CLEAN_TEXT)
    # 新实例（模拟重启）恢复状态
    mw2 = ChapterGateMiddleware(ws, model_access=_MockVerifyModel())
    st2 = mw2._chapters[CLEAN_CH]
    assert st2.verified == "unchecked"
    assert st2.reviewed is True
    assert st2.status == "unevaluated"


async def test_merge_gate_requires_verified_reviewed(tmp_path: Path) -> None:
    """审校 infra 失败 → 章保持未审校 → merge 硬失败不放行（Task 17 自动审校的失败面）。"""
    ws = make_ws(tmp_path, {CLEAN_CH: CLEAN_LONG})

    class _ReviewDown:
        async def ainvoke_text(self, prompt: str, **kwargs: object) -> str:
            if "审校" in str(kwargs.get("system") or ""):
                raise TimeoutError("review endpoint down")
            return '{"assertions": []}'

    mw = ChapterGateMiddleware(ws, model_access=_ReviewDown())
    mw._verify_backoff = 0
    await _stats(mw, f"chapters/{CLEAN_CH}")  # verified=passed，reviewed=False
    blocked = await _merge_call(mw)  # 自动审校 infra 失败 → 未审校硬失败
    assert blocked.status == "error"
    assert "未审校" in blocked.content
    assert "硬失败" in blocked.content
    mw.apply_review_edit(CLEAN_CH, CLEAN_LONG)  # 审校链恢复后补置 reviewed
    out = await _merge_call(mw)
    assert out.status != "error"


async def test_merge_gate_hard_blocks_pending_verification(tmp_path: Path) -> None:
    """核验事故（pending）→ merge 硬失败不放行（fail-closed，不消耗 merge_rounds）。"""
    ws = make_ws(tmp_path, {CLEAN_CH: CLEAN_TEXT})

    class _Down:
        async def ainvoke_text(self, prompt: str, **kwargs: object) -> str:
            raise TimeoutError("endpoint down")

    mw = ChapterGateMiddleware(ws, model_access=_Down())
    mw._verify_backoff = 0
    await _stats(mw, f"chapters/{CLEAN_CH}")
    assert mw._chapters[CLEAN_CH].verified == "pending"
    st = mw._chapters[CLEAN_CH]
    st.reviewed = True
    for _ in range(3):  # 硬失败无放行上限：反复 merge 始终 blocked
        out = await _merge_call(mw)
        assert out.status == "error"
    assert "未完成核验" in out.content
    assert mw._merge_rounds == 0  # 硬失败不消耗 merge 额度


# ---------------------------------------------------------------------------
# R4 Task 18：放行落盘追踪 + 附言终止语 + 未闭环复述（设计 §7.1/7.2，A4/A6）
# ---------------------------------------------------------------------------


async def _dirty_then_release(tmp_path: Path):
    """脏章阻断 2 次后第 3 次开新章放行；返回 (mw, ws, out)。"""
    ws = make_ws(tmp_path, {DIRTY_CH: DIRTY_TEXT})
    mw = ChapterGateMiddleware(ws)
    await _stats(mw, f"chapters/{DIRTY_CH}")  # 置脏
    for _ in range(2):
        blocked = await _write(mw, "write_file", f"chapters/{NEW_CH}")
        assert blocked.status == "error"
    out = await _write(mw, "write_file", f"chapters/{NEW_CH}")  # 第 3 次放行
    return mw, ws, out


async def test_release_findings_persisted(tmp_path: Path) -> None:
    mw, ws, out = await _dirty_then_release(tmp_path)
    assert out.status != "error"
    notes = (ws / "gate_release_notes.md").read_text(encoding="utf-8")
    assert DIRTY_CH in notes
    assert "未闭环" in notes
    assert "开新章放行" in notes


async def test_release_postscript_has_terminator(tmp_path: Path) -> None:
    _, _, out = await _dirty_then_release(tmp_path)
    assert "无需处理" in out.content  # A6 终止语：防模型把放行附言当继续修指令空转


async def test_next_merge_repeats_open_items(tmp_path: Path) -> None:
    mw, ws, _ = await _dirty_then_release(tmp_path)
    out = await mw.awrap_tool_call(_request("merge_chapters", {}), _handler())
    assert out.status == "error"  # 脏章本身拦 merge
    assert "未闭环放行条目复述" in out.content
    assert DIRTY_CH in out.content


async def test_open_items_closed_after_clean_merge(tmp_path: Path) -> None:
    mw, ws, _ = await _dirty_then_release(tmp_path)
    # 修复脏章 → merge 全量复测通过 → 未闭环条目核销（用非空壳长文，否则 structure 拦 merge）
    (ws / "chapters" / DIRTY_CH).write_text(CLEAN_LONG, encoding="utf-8")
    await _stats(mw, f"chapters/{DIRTY_CH}")
    out = await mw.awrap_tool_call(_request("merge_chapters", {}), _handler())
    assert out.status != "error"
    notes = (ws / "gate_release_notes.md").read_text(encoding="utf-8")
    assert "已闭环（复测通过）" in notes
    assert "| 未闭环 |" not in notes


async def test_startup_recaps_open_items(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    mw, ws, _ = await _dirty_then_release(tmp_path)
    with caplog.at_level(logging.WARNING, logger="octop_harness.middleware.chapter_gate"):
        ChapterGateMiddleware(ws)  # 新实例 = 任务重启
    assert any("未闭环放行条目" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# R4 Task 21：merge 旁路封堵（设计裁定 13）
# ---------------------------------------------------------------------------


async def test_direct_write_to_merged_report_blocked(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, {CLEAN_CH: CLEAN_TEXT})
    mw = ChapterGateMiddleware(ws)
    for tool in ("write_file", "edit_file"):
        out = await _write(mw, tool, "report_merged.md")
        assert out.status == "error"
        assert "merge_chapters" in out.content
    # 相对/虚拟/绝对路径都拦
    out = await _write(mw, "write_file", "/workspace/report_merged.md")
    assert out.status == "error"
    # 普通章节写入不受影响
    ok = await _write(mw, "write_file", f"chapters/{NEW_CH}")
    assert ok.status != "error"


# ---------------------------------------------------------------------------
# R4 Task 20：续写闭环 + 诚实出口举证制（设计 §7.4）
# ---------------------------------------------------------------------------

OUTLINE_FLOOR_03 = "# 大纲\n\n- 第3章 剖析：≥6,000字\n"

PROGRESS_EXIT_03 = """# 进度

## 出口申请

- 章：03_剖析.md
- 大纲要点覆盖：要点1→§3.2；要点2→§3.3
- 实际/目标：17 / 6,000
- 不足原因：公开信息已穷尽
"""


class _ExitModel:
    """三角色桩：出口核实 → granted 参数；审校 → 空编辑；核验抽取 → 空清单。"""

    def __init__(self, granted: bool, reason: str = "映射真实") -> None:
        self._granted = granted
        self._reason = reason

    async def ainvoke_text(self, prompt: str, **kwargs: object) -> str:
        system = str(kwargs.get("system") or "")
        if "诚实出口" in system:
            return json.dumps({"granted": self._granted, "reason": self._reason}, ensure_ascii=False)
        if "审校" in system:
            return '{"edits": [], "doubts": []}'
        return '{"assertions": []}'


async def test_below_floor_triggers_continuation_postscript(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, {CLEAN_CH: CLEAN_TEXT})
    (ws / "outline.md").write_text(OUTLINE_FLOOR_03, encoding="utf-8")
    mw = ChapterGateMiddleware(ws)
    out = await _stats(mw, f"chapters/{CLEAN_CH}")
    st = mw._chapters[CLEAN_CH]
    assert st.status == "dirty"
    assert "字数不足" in out.content
    assert "出口申请" in out.content  # 附言指引含诚实出口路径


async def test_missing_floor_skips_chapter(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, {CLEAN_CH: CLEAN_TEXT})
    (ws / "outline.md").write_text("# 大纲\n\n- 第9章 其他：≥6,000字\n", encoding="utf-8")
    mw = ChapterGateMiddleware(ws)
    out = await _stats(mw, f"chapters/{CLEAN_CH}")
    assert "本章检查通过" in out.content


async def test_extend_rounds_hard_cap_3(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, {CLEAN_CH: CLEAN_TEXT})
    (ws / "outline.md").write_text(OUTLINE_FLOOR_03, encoding="utf-8")
    mw = ChapterGateMiddleware(ws)
    await _stats(mw, f"chapters/{CLEAN_CH}")  # 字数不足置脏
    st = mw._chapters[CLEAN_CH]
    for i in range(1, 4):
        blocked = await _write(mw, "write_file", f"chapters/{NEW_CH}")
        assert blocked.status == "error"
        assert "续写" in blocked.content
        assert st.extend_rounds == i
        assert st.repair_rounds == 0  # 预算分账：续写不消耗修复 N=2
    released = await _write(mw, "write_file", f"chapters/{NEW_CH}")
    assert released.status != "error"  # 第 4 次放行
    notes = (ws / "gate_release_notes.md").read_text(encoding="utf-8")
    assert "续写上限放行" in notes


async def test_exit_granted_after_review_verifies_evidence(tmp_path: Path) -> None:
    # 章体过空壳线（≥500）但低于 outline 下限 6000 —— 诚实出口的真实场景
    ws = make_ws(tmp_path, {CLEAN_CH: CLEAN_LONG})
    (ws / "outline.md").write_text(OUTLINE_FLOOR_03, encoding="utf-8")
    (ws / "progress.md").write_text(PROGRESS_EXIT_03, encoding="utf-8")
    mw = ChapterGateMiddleware(ws, model_access=_ExitModel(True))
    mw._verify_backoff = 0
    await _stats(mw, f"chapters/{CLEAN_CH}")  # dirty（字数不足）
    out = await mw.awrap_tool_call(_request("merge_chapters", {}), _handler())
    assert out.status != "error"  # 举证通过 → merge 放行
    st = mw._chapters[CLEAN_CH]
    assert st.exit_granted is True
    # 终审修复 1b：出口只豁免字数下限，真实性工序（核验/审校）仍须完成
    assert st.verified == "passed"
    assert st.reviewed is True
    notes = (ws / "gate_release_notes.md").read_text(encoding="utf-8")
    assert "诚实出口" in notes  # 合规但记录


async def test_exit_rejected_blocks_merge(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, {CLEAN_CH: CLEAN_LONG})
    (ws / "outline.md").write_text(OUTLINE_FLOOR_03, encoding="utf-8")
    (ws / "progress.md").write_text(PROGRESS_EXIT_03, encoding="utf-8")
    mw = ChapterGateMiddleware(ws, model_access=_ExitModel(False, "映射虚构：文中无 §3.3"))
    mw._verify_backoff = 0
    await _stats(mw, f"chapters/{CLEAN_CH}")
    out = await mw.awrap_tool_call(_request("merge_chapters", {}), _handler())
    assert out.status == "error"
    assert "出口举证被驳回" in out.content
    assert mw._chapters[CLEAN_CH].exit_granted is False


# ---------------------------------------------------------------------------
# R4 终审修复 1a：审校改文后必须重新核验（终审复现：2020 被改成 2099）
# ---------------------------------------------------------------------------

LISTING_2020 = "欧普照明于 2020 年在上海证券交易所主板上市，股票代码 603515[1]。"
LISTING_2099 = "欧普照明于 2099 年在上海证券交易所主板上市，股票代码 603515[1]。"
_REF_SECTION = "\n\n## 本章参考资料\n\n[1] 欧普照明年度报告，上海证券交易所披露平台。\n"
CHAPTER_2020 = LISTING_2020 + "\n" + CLEAN_LONG + _REF_SECTION


async def _search_ok(query: str) -> list[dict]:
    return [{"url": "https://example.com/official", "title": "t", "snippet": "s"}]


async def _fetch_ok(url: str) -> str:
    return "官方披露正文：欧普照明 2020 年在上海证券交易所主板上市。"


class _ReviewCorruptsYear:
    """四角色桩：抽取空（走关键词通路）；判定：提示词含 2099 → 证伪，否则证实；
    审校首次把上市年份 2020 改成 2099（此后不再改）；记录全部判定提示词。"""

    def __init__(self) -> None:
        self.judge_prompts: list[str] = []
        self._reviewed = False

    async def ainvoke_text(self, prompt: str, **kwargs: object) -> str:
        system = str(kwargs.get("system") or "")
        if "审校" in system:
            if self._reviewed:
                return '{"edits": [], "doubts": []}'
            self._reviewed = True
            return json.dumps(
                {
                    "edits": [
                        {"line": 1, "new": LISTING_2099, "reason": "模拟审校引入事实错误"}
                    ],
                    "doubts": [],
                },
                ensure_ascii=False,
            )
        if "抽取" in system:
            return '{"assertions": []}'
        self.judge_prompts.append(prompt)
        if "2099" in prompt:
            return json.dumps(
                {"verdict": "refuted", "reason": "官方披露为 2020 年"}, ensure_ascii=False
            )
        return json.dumps({"verdict": "confirmed", "reason": "官方披露一致"}, ensure_ascii=False)


async def test_review_edit_forces_reverification(tmp_path: Path) -> None:
    """终审复现：审校把上市年份 2020 改成 2099 —— 第一次 merge 拦（审校已改），
    第二次 merge 必须重新核验并拦下证伪断言（修复前：第二次直接放行，2099 进报告）。"""
    ws = make_ws(tmp_path, {CLEAN_CH: CHAPTER_2020})
    stub = _ReviewCorruptsYear()
    mw = ChapterGateMiddleware(
        ws, model_access=stub, verify_search=_search_ok, verify_fetch=_fetch_ok
    )
    mw._verify_backoff = 0
    await _stats(mw, f"chapters/{CLEAN_CH}")
    assert mw._chapters[CLEAN_CH].verified == "passed"

    first = await _merge_call(mw)
    assert first.status == "error"
    assert "审校已改" in first.content
    assert LISTING_2099 in (ws / "chapters" / CLEAN_CH).read_text(encoding="utf-8")
    assert mw._chapters[CLEAN_CH].verified == "unchecked"  # 文本已变，核验结论作废

    second = await _merge_call(mw)
    assert second.status == "error"
    assert "证伪" in second.content
    assert any("2099" in p for p in stub.judge_prompts)  # 确实重核了改后文本


# ---------------------------------------------------------------------------
# R4 终审修复 1b：诚实出口只豁免字数下限，不豁免核验/审校
# ---------------------------------------------------------------------------


class _ExitButVerifyDown:
    """出口核实 granted + 审校空编辑 + 核验抽取 infra 失败（verified=pending）。"""

    async def ainvoke_text(self, prompt: str, **kwargs: object) -> str:
        system = str(kwargs.get("system") or "")
        if "诚实出口" in system:
            return '{"granted": true, "reason": "映射真实"}'
        if "审校" in system:
            return '{"edits": [], "doubts": []}'
        raise TimeoutError("verify endpoint down")


async def test_exit_granted_does_not_skip_verification(tmp_path: Path) -> None:
    """终审复现：字数不足章出口举证通过即绕过真实性工序（verified=unchecked 也能合并）。
    修复后：核验缺失（pending）的出口章 merge 硬失败，且硬失败不消耗 merge 额度。"""
    ws = make_ws(tmp_path, {CLEAN_CH: CLEAN_LONG})
    (ws / "outline.md").write_text(OUTLINE_FLOOR_03, encoding="utf-8")
    (ws / "progress.md").write_text(PROGRESS_EXIT_03, encoding="utf-8")
    mw = ChapterGateMiddleware(ws, model_access=_ExitButVerifyDown())
    mw._verify_backoff = 0
    await _stats(mw, f"chapters/{CLEAN_CH}")  # dirty（字数不足）；核验 infra → pending
    out = await _merge_call(mw)  # 出口核实通过（exit_granted=True）但核验缺失
    assert out.status == "error"
    assert "未完成核验" in out.content
    st = mw._chapters[CLEAN_CH]
    assert st.exit_granted is True  # 出口成立 ≠ 真实性工序豁免
    assert st.verified == "pending"
    assert mw._merge_rounds == 0


# ---------------------------------------------------------------------------
# R4 终审修复 1c：查无实证必须降级/标注 [待核实] 并复测后才放行
# ---------------------------------------------------------------------------

UNVERIFIABLE_CH = "08_并购.md"
ACQ_SENTENCE = "欧普照明于 2019 年完成对某照明企业的重大收购[1]。"
ACQ_MARKED = "欧普照明于 2019 年完成对某照明企业的重大收购[1][待核实]。"
CHAPTER_ACQ = ACQ_SENTENCE + "\n" + CLEAN_LONG + _REF_SECTION
CHAPTER_ACQ_MARKED = ACQ_MARKED + "\n" + CLEAN_LONG + _REF_SECTION


class _UnverifiableModel:
    """三角色桩：抽取空（走关键词通路）；判定恒 unverifiable；审校空编辑。"""

    async def ainvoke_text(self, prompt: str, **kwargs: object) -> str:
        system = str(kwargs.get("system") or "")
        if "审校" in system:
            return '{"edits": [], "doubts": []}'
        if "抽取" in system:
            return '{"assertions": []}'
        return json.dumps(
            {"verdict": "unverifiable", "reason": "公开检索无相关信息"}, ensure_ascii=False
        )


async def test_unverifiable_blocks_merge_until_marked(tmp_path: Path) -> None:
    """终审复现：查无实证仅进 verify_notes、verified=passed，补审校后四次 merge 均成功。
    修复后：notes 未清零 merge 硬失败；标注 [待核实] 并复测重核后放行。"""
    ws = make_ws(tmp_path, {UNVERIFIABLE_CH: CHAPTER_ACQ})
    mw = ChapterGateMiddleware(
        ws, model_access=_UnverifiableModel(), verify_search=_search_ok, verify_fetch=_fetch_ok
    )
    mw._verify_backoff = 0
    out = await _stats(mw, f"chapters/{UNVERIFIABLE_CH}")
    st = mw._chapters[UNVERIFIABLE_CH]
    assert st.verified == "passed"
    assert ACQ_SENTENCE in st.verify_notes
    assert "待核实" in out.content  # 附言指引仍在

    for _ in range(2):  # 硬失败可重复，不消耗 merge 额度
        blocked = await _merge_call(mw)
        assert blocked.status == "error"
        assert "查无实证" in blocked.content
    assert mw._merge_rounds == 0

    # 模型按指引标注 [待核实]（编辑章 → 状态清零 → merge 前补核重验 → 已标注断言记为已降级）
    (ws / "chapters" / UNVERIFIABLE_CH).write_text(CHAPTER_ACQ_MARKED, encoding="utf-8")
    await _write(mw, "edit_file", f"chapters/{UNVERIFIABLE_CH}")
    ok = await _merge_call(mw)
    assert ok.status != "error"
    assert mw._chapters[UNVERIFIABLE_CH].verify_notes == []


# ---------------------------------------------------------------------------
# R4 终审复验补修：台账反查断言按正文标注出队
# ---------------------------------------------------------------------------

LEDGER_WITH_ACQ = LEDGER + "| 收购事项 | 未披露 | — | — | 年报 | 08 |\n"


async def test_ledger_assertion_cleared_when_body_marked(tmp_path: Path) -> None:
    """终审复验复现：正文已标 [待核实]，但台账反查断言「收购事项：未披露」字符串
    不含标注仍入 verify_notes，merge 继续被拦。修复后：正文已处置的同断言出队放行。"""
    ws = make_ws(tmp_path, {UNVERIFIABLE_CH: CHAPTER_ACQ_MARKED}, ledger=LEDGER_WITH_ACQ)
    mw = ChapterGateMiddleware(
        ws, model_access=_UnverifiableModel(), verify_search=_search_ok, verify_fetch=_fetch_ok
    )
    mw._verify_backoff = 0
    await _stats(mw, f"chapters/{UNVERIFIABLE_CH}")
    assert mw._chapters[UNVERIFIABLE_CH].verify_notes == []
    ok = await _merge_call(mw)
    assert ok.status != "error"
