"""Meerkat 报告章节门禁：report_stats 章验收触点 + 开新章阻断 + merge 终闸。

设计依据：`docs/meerkat/v1.0.5/design/设计文档_R3.md` §4。零内核——复用
report_qa 既有检查器，状态机全部收在本中间件内，经 config flag
（``chapter_gate_enabled``，默认关）挂载。

状态机（两个触点 + 一个终闸）：

- ``report_stats(chapters/NN.md)``：模型自认本章写完 → 跑章级检查（四检查器
  过滤本章）→ findings 以 ``[chapter_gate]`` 附言追加进 ToolMessage →
  状态置 clean / dirty；
- ``write_file`` / ``edit_file`` 开新章（目标 chapters/*.md 尚不存在）：
  有脏章且其 repair_rounds < max → 阻断（ToolMessage status=error），
  repair_rounds +1；达上限 → 放行 + logger.warning + 放行附言。
  修改已存在的章节永远不拦（修复路径），并把该章置回 unevaluated；
  非章节文件（progress/ledger/outline 等）永远不拦；
- ``merge_chapters``：全量检查（四检查器 + numeric_consistency + structure），
  findings > 0 且 merge_rounds < max → 阻断；达上限 → 放行 + logger.warning。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from octop_harness.report_qa.checks import CHECKERS

logger = logging.getLogger(__name__)

# 章验收触点检查器（设计 §4.2 + R4 §2.2 引用三级判定进门禁）
CHAPTER_CHECKERS = (
    "unsourced_precise",
    "figure_companion",
    "ledger_freshness",
    "degeneration",
    "citation_dangling",
    "citation_orphan",
    "citation_ledger_ref",
    "placeholder_scan",
)
# merge 终闸：四检查器 + 数值一致性（M1 修正后）+ 结构
MERGE_CHECKERS = CHAPTER_CHECKERS + ("numeric_consistency", "structure")

_WRITE_TOOLS = ("write_file", "edit_file")
_STATS_TOOL = "report_stats"
_MERGE_TOOL = "merge_chapters"

# 章节路径：相对（chapters/NN.md）、虚拟（/chapters/NN.md）、绝对均可
_CHAPTER_RE = re.compile(r"(?:^|[/\\])chapters[/\\]([^/\\]+\.md)$")

_HINTS = {
    "unsourced_precise": "补来源标注或标 [待核实]",
    "degeneration": "重写重复段，禁止复制凑字数",
    "figure_companion": "补来源题注与解读",
    "ledger_freshness": "先登记 data_ledger.md",
    "numeric_consistency": "统一台账数值口径",
    "structure": "修复结构问题",
}

_GD_TAG_RE = re.compile(r"（GD-[^）]*）")


@dataclass
class _ChapterState:
    status: str = "unevaluated"  # unevaluated | clean | dirty
    findings: list[dict[str, Any]] = field(default_factory=list)
    repair_rounds: int = 0  # 因该章脏而被阻断开新章的次数


def _tool_meta(request: ToolCallRequest) -> tuple[str, str, dict[str, Any]]:
    tool_call = request.tool_call
    args = tool_call.get("args") or {}
    return (
        str(tool_call.get("name") or ""),
        str(tool_call.get("id") or ""),
        args if isinstance(args, dict) else {},
    )


def _chapter_name_from_path(raw: Any) -> str | None:
    """从工具路径参数中识别 chapters/*.md 章文件名；非章节路径返回 None。"""
    if not isinstance(raw, str) or not raw.strip():
        return None
    m = _CHAPTER_RE.search(raw.strip())
    return m.group(1) if m else None


def _short(message: str, limit: int = 60) -> str:
    text = _GD_TAG_RE.sub("", message).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _is_error(result: Any) -> bool:
    return isinstance(result, ToolMessage) and result.status == "error"


class ChapterGateMiddleware(AgentMiddleware[Any, Any]):
    """章节门禁中间件（默认经 ``chapter_gate_enabled`` 挂载，默认关）。"""

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        max_repairs: int = 2,
    ) -> None:
        self._workspace = Path(workspace_root)
        self._max = max_repairs
        self._chapters: dict[str, _ChapterState] = {}
        self._merge_rounds = 0
        self._released: tuple[str, str] | None = None  # (脏章, 新开章) 放行附言
        self._merge_released: int | None = None  # merge 放行时遗留 findings 数

    # ------------------------------------------------------------------
    # 评估
    # ------------------------------------------------------------------

    def _evaluate_chapter(self, chapter: str) -> list[dict[str, Any]]:
        rel = f"chapters/{chapter}"
        findings: list[dict[str, Any]] = []
        for name in CHAPTER_CHECKERS:
            for f in CHECKERS[name](self._workspace):
                if f["file"] == rel:
                    findings.append(f)
        return findings

    def _evaluate_all(self) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        for name in MERGE_CHECKERS:
            findings.extend(CHECKERS[name](self._workspace))
        return findings

    # ------------------------------------------------------------------
    # 附言
    # ------------------------------------------------------------------

    @staticmethod
    def _finding_line(f: dict[str, Any], *, with_file: bool) -> str:
        hint = _HINTS.get(f["check"], "修复后复测")
        where = f"{f['file']} " if with_file else ""
        return f"- {where}行 {f['line']}：{_short(f['message'])} → {hint}"

    def _chapter_postscript(self, findings: list[dict[str, Any]]) -> str:
        if not findings:
            return "[chapter_gate] 本章检查通过，可继续下一章。"
        lines = [f"[chapter_gate] 本章检查发现 {len(findings)} 项："]
        lines.extend(self._finding_line(f, with_file=False) for f in findings[:10])
        if len(findings) > 10:
            lines.append(f"- …另有 {len(findings) - 10} 项（同类问题一并处理）")
        lines.append("修复后请再次调用 report_stats 复测。")
        return "\n".join(lines)

    def _multi_postscript(
        self, results: list[tuple[str, list[dict[str, Any]]]]
    ) -> str:
        """目录统计（report_stats chapters/）的合并附言：逐章状态 + 脏章明细。"""
        dirty = [(ch, fs) for ch, fs in results if fs]
        header = (
            f"[chapter_gate] 本次检查 {len(results)} 章："
            f"{len(results) - len(dirty)} 章通过，{len(dirty)} 章有问题。"
        )
        if not dirty:
            return header + "可继续下一章。"
        lines = [header]
        budget = 10
        for ch, fs in dirty:
            lines.append(f"- {ch}（{len(fs)} 项）：")
            for f in fs[:budget]:
                lines.append("  " + self._finding_line(f, with_file=False))
            budget -= len(fs)
            if budget <= 0:
                lines.append("  - …（余项修复后复测查看）")
                break
        lines.append("修复后请再次调用 report_stats 复测。")
        return "\n".join(lines)

    def _merge_summary(self, findings: list[dict[str, Any]]) -> str:
        lines = [
            f"[chapter_gate] 合并闸门：全量检查发现 {len(findings)} 项问题，"
            "先修复再合并（余项可在修复后整体复测）："
        ]
        lines.extend(self._finding_line(f, with_file=True) for f in findings[:10])
        if len(findings) > 10:
            lines.append(f"- …另有 {len(findings) - 10} 项")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 状态机
    # ------------------------------------------------------------------

    def _dirty_chapters(self) -> list[str]:
        return sorted(n for n, st in self._chapters.items() if st.status == "dirty")

    def _chapter_exists(self, chapter: str) -> bool:
        """开新章判定：章文件是否已存在（dry-run 可覆写为虚拟时序）。"""
        return (self._workspace / "chapters" / chapter).is_file()

    def _all_workspace_chapters(self) -> list[str]:
        ch_dir = self._workspace / "chapters"
        if not ch_dir.is_dir():
            return []
        return sorted(p.name for p in ch_dir.glob("*.md"))

    def _chapter_state(self, chapter: str) -> _ChapterState:
        return self._chapters.setdefault(chapter, _ChapterState())

    def _pre_tool_call(self, request: ToolCallRequest) -> ToolMessage | None:
        """阻断判定（write/edit 开新章、merge 终闸）。返回 None = 放行。"""
        tool_name, call_id, args = _tool_meta(request)
        base = tool_name.rsplit("/", 1)[-1]

        if base in _WRITE_TOOLS:
            chapter = _chapter_name_from_path(
                args.get("file_path") or args.get("path")
            )
            if chapter is None:
                return None
            if self._chapter_exists(chapter):
                return None  # 已存在章节：修改/填充永远放行（含骨架+填充同窗口）
            dirty = self._dirty_chapters()
            if not dirty:
                return None
            first = dirty[0]
            st = self._chapter_state(first)
            if st.repair_rounds >= self._max:
                logger.warning(
                    "chapter_gate: 第 %s 章已达自修上限（%d 次），放行开新章 %s；"
                    "遗留 findings %d 条交完稿评审",
                    first,
                    st.repair_rounds,
                    chapter,
                    len(st.findings),
                )
                self._released = (first, chapter)  # post 阶段追加放行附言
                return None
            st.repair_rounds += 1
            logger.info(
                "chapter_gate: 阻断开新章 %s（第 %s 章有 %d 条未修复问题，第 %d 次拦截）",
                chapter,
                first,
                len(st.findings),
                st.repair_rounds,
            )
            return ToolMessage(
                content=(
                    f"[chapter_gate] 门禁：第 {first.split('_')[0]} 章（{first}）有 "
                    f"{len(st.findings)} 条未修复问题，先修复再继续。"
                    "详情见上次 report_stats 返回的 [chapter_gate] 附言；"
                    "修复该章后再次调用 report_stats 复测，通过后方可开新章。"
                ),
                name=tool_name or "tool",
                tool_call_id=call_id,
                status="error",
            )

        if base == _MERGE_TOOL:
            findings = self._evaluate_all()
            if not findings:
                return None
            if self._merge_rounds >= self._max:
                logger.warning(
                    "chapter_gate: merge 闸已达自修上限（%d 次），放行合并；"
                    "遗留 findings %d 条交完稿评审",
                    self._merge_rounds,
                    len(findings),
                )
                self._merge_released = len(findings)
                return None
            self._merge_rounds += 1
            logger.info(
                "chapter_gate: 阻断 merge_chapters（%d 条 findings，第 %d 次拦截）",
                len(findings),
                self._merge_rounds,
            )
            return ToolMessage(
                content=self._merge_summary(findings),
                name=tool_name or "tool",
                tool_call_id=call_id,
                status="error",
            )

        return None

    def _post_tool_call(
        self,
        request: ToolCallRequest,
        result: ToolMessage | Command[Any],
    ) -> ToolMessage | Command[Any]:
        """后置处理：report_stats 章评估 + 附言；写章成功置 unevaluated；放行附言。"""
        tool_name, _, args = _tool_meta(request)
        base = tool_name.rsplit("/", 1)[-1]

        if base == _STATS_TOOL and not _is_error(result):
            chapter = _chapter_name_from_path(args.get("path"))
            # 单章统计评估单章；目录统计（chapters/）评估全部已存在章——
            # 模型实测习惯是目录统计，门禁必须挂这个已保持的行为。
            chapters = [chapter] if chapter is not None else self._all_workspace_chapters()
            if chapters and isinstance(result, ToolMessage):
                results: list[tuple[str, list[dict[str, Any]]]] = []
                for ch in chapters:
                    findings = self._evaluate_chapter(ch)
                    st = self._chapter_state(ch)
                    st.findings = findings
                    st.status = "dirty" if findings else "clean"
                    if not findings:
                        st.repair_rounds = 0  # 复测通过，额度复位
                    results.append((ch, findings))
                ps = (
                    self._chapter_postscript(results[0][1])
                    if chapter is not None
                    else self._multi_postscript(results)
                )
                result = ToolMessage(
                    content=f"{result.content}\n\n{ps}",
                    name=result.name,
                    tool_call_id=result.tool_call_id,
                    status=result.status,
                )
            return result

        if base in _WRITE_TOOLS and not _is_error(result):
            chapter = _chapter_name_from_path(
                args.get("file_path") or args.get("path")
            )
            if chapter is not None:
                st = self._chapter_state(chapter)
                st.status = "unevaluated"  # 修改脏章/任何章 → 待复测
                st.findings = []
            released = self._released
            if released is not None and isinstance(result, ToolMessage):
                first, _ = released
                result = ToolMessage(
                    content=(
                        f"{result.content}\n\n[chapter_gate] 第 {first.split('_')[0]} 章"
                        f"已达自修上限（{self._max} 次），本次放行并记录，"
                        "遗留问题交完稿评审清点。"
                    ),
                    name=result.name,
                    tool_call_id=result.tool_call_id,
                    status=result.status,
                )
            self._released = None
            return result

        if base == _MERGE_TOOL:
            released_n = self._merge_released
            if released_n is not None and isinstance(result, ToolMessage):
                result = ToolMessage(
                    content=(
                        f"{result.content}\n\n[chapter_gate] 合并闸门已达自修上限"
                        f"（{self._max} 次），本次放行并记录，"
                        f"遗留 {released_n} 条问题交完稿评审清点。"
                    ),
                    name=result.name,
                    tool_call_id=result.tool_call_id,
                    status=result.status,
                )
            self._merge_released = None
            return result

        return result

    # ------------------------------------------------------------------
    # wrap_tool_call
    # ------------------------------------------------------------------

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        blocked = self._pre_tool_call(request)
        if blocked is not None:
            return blocked
        result = await handler(request)
        return self._post_tool_call(request, result)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        blocked = self._pre_tool_call(request)
        if blocked is not None:
            return blocked
        result = handler(request)
        return self._post_tool_call(request, result)


__all__ = [
    "CHAPTER_CHECKERS",
    "MERGE_CHECKERS",
    "ChapterGateMiddleware",
]
