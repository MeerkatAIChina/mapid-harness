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

import asyncio
import json
import logging
import random
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from octop_harness.middleware.review_chain import apply_edits, run_review
from octop_harness.middleware.verification import (
    extract_loadbearing_assertions,
    sample_numeric_claims,
    verify_assertion,
    with_retry,
)
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
    "intra_chapter_redundancy",
)
# merge 终闸：四检查器 + 数值一致性（M1 修正后）+ 结构
MERGE_CHECKERS = CHAPTER_CHECKERS + ("numeric_consistency", "structure")

_WRITE_TOOLS = ("write_file", "edit_file")
_STATS_TOOL = "report_stats"
_MERGE_TOOL = "merge_chapters"

# 章节路径：相对（chapters/NN.md）、虚拟（/chapters/NN.md）、绝对均可
_CHAPTER_RE = re.compile(r"(?:^|[/\\])chapters[/\\]([^/\\]+\.md)$")

_HINTS = {
    "unsourced_precise": "补来源标注或标 [待核实]（正确写法如：营收 70.96 亿元[1]，章末「## 本章参考资料」列出处）",
    "degeneration": "重写重复段，禁止复制凑字数",
    "figure_companion": "补来源题注与解读",
    "ledger_freshness": "先登记 data_ledger.md",
    "numeric_consistency": "统一台账数值口径",
    "structure": "修复结构问题",
    "verification": "证伪断言必须删改",
}

_GD_TAG_RE = re.compile(r"（GD-[^）]*）")


async def _default_search(query: str) -> list[dict[str, Any]]:
    """searchfree 检索（零配置）；上游不可用抛异常 = infra 失败。"""
    from octop_harness.builtin.tools.web_search.searchfree import searchfree_search

    raw = await searchfree_search.ainvoke({"query": query, "max_results": 5})
    if raw.startswith("Error:"):
        raise RuntimeError(raw)
    data = json.loads(raw)
    return data if isinstance(data, list) else list(data.get("results", []))


async def _default_fetch(url: str) -> str:
    """正文抓取复用既有 web_fetch（SSRF 防护现成）；同步 httpx 以 to_thread 包装。"""
    from octop_harness.builtin.tools.web_fetch import web_fetch

    return await asyncio.to_thread(web_fetch.invoke, {"url": url})


@dataclass
class _ChapterState:
    status: str = "unevaluated"  # unevaluated | clean | dirty
    findings: list[dict[str, Any]] = field(default_factory=list)
    repair_rounds: int = 0  # 因该章脏而被阻断开新章的次数
    # R4 §3.1 核验维度：unchecked | passed | failed | pending（infra fail-open）
    verified: str = "unchecked"
    verify_findings: list[dict[str, Any]] = field(default_factory=list)  # 证伪断言
    verify_notes: list[str] = field(default_factory=list)  # 查无实证 → 降级要求
    reviewed: bool = False  # R4 §3.2 审校维度（审校链置位）
    exit_granted: bool = False  # R4 §7.2 诚实出口举证通过（Task 20 置位）


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
        model_access: Any | None = None,
        verify_search: Callable[[str], Awaitable[list[dict[str, Any]]]] | None = None,
        verify_fetch: Callable[[str], Awaitable[str]] | None = None,
    ) -> None:
        self._workspace = Path(workspace_root)
        self._max = max_repairs
        self._model_access = model_access
        self._search = verify_search or _default_search
        self._fetch = verify_fetch or _default_fetch
        self._chapters: dict[str, _ChapterState] = {}
        self._merge_rounds = 0
        self._released: tuple[str, str] | None = None  # (脏章, 新开章) 放行附言
        self._merge_released: list[dict[str, Any]] | None = None  # merge 放行时遗留 findings
        self._infra_streak = 0  # 连续 infra 失败次数（核验事故计数器，与 N=2 分开）
        self._verify_aborted = False  # 连续 3 次 infra 失败 = 事故中止，本轮停止核验
        self._verify_backoff = 1.0  # 重试退避基数（测试可置 0）
        self._rng = random.Random()
        self._auto_reviewed: list[tuple[str, int]] = []  # merge 前自动审校改动（章, 处数）
        self._restore_state()
        open_items = self._open_release_items()
        if open_items:
            logger.warning(
                "chapter_gate: 启动复述——%d 条未闭环放行条目待完稿评审：%s",
                len(open_items),
                "；".join(f"{it['chapter']}:{it['item'][:30]}" for it in open_items[:5]),
            )

    # ------------------------------------------------------------------
    # 放行追踪落盘（设计_R4 §7.1，A4）
    # ------------------------------------------------------------------

    _RELEASE_FILE = "gate_release_notes.md"
    _RELEASE_HEADER = (
        "# 章节门禁放行记录\n\n| 时间 | 闸门 | 章 | 条目 | 终态 |\n| --- | --- | --- | --- | --- |\n"
    )
    _RELEASE_TERMINATOR = "以下问题已记录，交完稿评审，**无需处理**。"

    def _parse_release_notes(self) -> list[dict[str, str]]:
        path = self._workspace / self._RELEASE_FILE
        if not path.is_file():
            return []
        rows: list[dict[str, str]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if len(cells) == 5 and cells[0][:1].isdigit():
                    rows.append(
                        dict(zip(("ts", "gate", "chapter", "item", "status"), cells, strict=True))
                    )
        except OSError:
            return []
        return rows

    def _open_release_items(self) -> list[dict[str, str]]:
        return [r for r in self._parse_release_notes() if r["status"] == "未闭环"]

    def _append_release_notes(self, gate: str, findings: list[dict[str, Any]]) -> None:
        if not findings:
            return
        path = self._workspace / self._RELEASE_FILE
        try:
            if not path.is_file():
                path.write_text(self._RELEASE_HEADER, encoding="utf-8")
            ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with path.open("a", encoding="utf-8") as fh:
                for f in findings:
                    item = f"行 {f.get('line', 0)}：{_short(f.get('message', ''))}".replace("|", "/")
                    chapter = str(f.get("file", "")).removeprefix("chapters/")
                    fh.write(f"| {ts} | {gate} | {chapter} | {item} | 未闭环 |\n")
        except OSError:
            logger.warning("chapter_gate: gate_release_notes.md 写入失败", exc_info=True)

    def _close_release_items(self, clean_files: set[str]) -> None:
        """merge 全量复测无 findings 的章，其未闭环条目标记闭环。"""
        rows = self._parse_release_notes()
        if not rows:
            return
        changed = False
        for r in rows:
            if r["status"] == "未闭环" and f"chapters/{r['chapter']}" in clean_files:
                r["status"] = "已闭环（复测通过）"
                changed = True
        if changed:
            try:
                lines = [self._RELEASE_HEADER]
                lines += [
                    f"| {r['ts']} | {r['gate']} | {r['chapter']} | {r['item']} | {r['status']} |\n"
                    for r in rows
                ]
                (self._workspace / self._RELEASE_FILE).write_text("".join(lines), encoding="utf-8")
            except OSError:
                pass

    def _open_items_recap(self) -> str:
        """未闭环条目复述（merge 闸强制复述，设计 §7.1）。"""
        open_items = self._open_release_items()
        if not open_items:
            return ""
        lines = ["未闭环放行条目复述（交完稿评审清点）："]
        lines.extend(f"- {r['chapter']}：{r['item']}" for r in open_items[:10])
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 状态落盘 / 恢复（设计_R4 §3.3：重启不丢，调研坑①）
    # ------------------------------------------------------------------

    _STATE_FILE = "gate_state.json"

    def _persist_state(self) -> None:
        """_chapters 全量状态落盘（机器可读 JSON；gate_release_notes.md 是人读放行记录）。"""
        data = {
            "chapters": {
                name: {
                    "status": st.status,
                    "verified": st.verified,
                    "reviewed": st.reviewed,
                    "exit_granted": st.exit_granted,
                    "repair_rounds": st.repair_rounds,
                    "findings": st.findings,
                    "verify_findings": st.verify_findings,
                    "verify_notes": st.verify_notes,
                }
                for name, st in self._chapters.items()
            },
            "merge_rounds": self._merge_rounds,
            "infra_streak": self._infra_streak,
            "verify_aborted": self._verify_aborted,
        }
        try:
            (self._workspace / self._STATE_FILE).write_text(
                json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except OSError:
            logger.warning("chapter_gate: 状态落盘失败", exc_info=True)

    def _restore_state(self) -> None:
        path = self._workspace / self._STATE_FILE
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for name, raw in data.get("chapters", {}).items():
                st = _ChapterState()
                st.status = raw.get("status", "unevaluated")
                st.verified = raw.get("verified", "unchecked")
                st.reviewed = bool(raw.get("reviewed", False))
                st.exit_granted = bool(raw.get("exit_granted", False))
                st.repair_rounds = int(raw.get("repair_rounds", 0))
                st.findings = list(raw.get("findings", []))
                st.verify_findings = list(raw.get("verify_findings", []))
                st.verify_notes = list(raw.get("verify_notes", []))
                self._chapters[name] = st
            self._merge_rounds = int(data.get("merge_rounds", 0))
            self._infra_streak = int(data.get("infra_streak", 0))
            self._verify_aborted = bool(data.get("verify_aborted", False))
        except (OSError, ValueError, TypeError):
            logger.warning("chapter_gate: 状态恢复失败，按全新状态启动", exc_info=True)

    def apply_review_edit(self, chapter: str, new_text: str) -> None:
        """审校工序自身改动（设计裁定 14 豁免「编辑即清」）：落盘 + 置 reviewed，
        verified 保留（事实疑点走核验队列，不由审校判）；章置 unevaluated 待复测。"""
        (self._workspace / "chapters" / chapter).write_text(new_text, encoding="utf-8")
        st = self._chapter_state(chapter)
        st.reviewed = True
        st.status = "unevaluated"
        st.findings = []
        self._persist_state()

    def mark_reviewed(self, chapter: str) -> None:
        """审校跑完无需改动：仅置 reviewed。"""
        self._chapter_state(chapter).reviewed = True
        self._persist_state()

    # ------------------------------------------------------------------
    # 审校链（R4 §3.2，merge 前自动触发）
    # ------------------------------------------------------------------

    _AUDIT_FILE = "review_audit.jsonl"
    _QUEUE_FILE = "verification_queue.jsonl"

    def _log_audit(self, entry: dict[str, Any]) -> None:
        entry = {"ts": datetime.now(timezone.utc).isoformat(), **entry}
        try:
            with (self._workspace / self._AUDIT_FILE).open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            logger.warning("chapter_gate: review_audit.jsonl 写入失败", exc_info=True)

    def _enqueue_doubts(self, chapter: str, doubts: list[str]) -> None:
        """事实疑点回流核验队列（设计 §3.2：审校不独自判）。"""
        if not doubts:
            return
        try:
            with (self._workspace / self._QUEUE_FILE).open("a", encoding="utf-8") as fh:
                for d in doubts:
                    fh.write(
                        json.dumps(
                            {
                                "ts": datetime.now(timezone.utc).isoformat(),
                                "chapter": chapter,
                                "doubt": d,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
        except OSError:
            logger.warning("chapter_gate: verification_queue.jsonl 写入失败", exc_info=True)

    def _read_doubts(self, chapter: str) -> list[str]:
        """读本章疑点队列（不移除；核验成功后由 _clear_doubts 清除，infra 失败不丢疑点）。"""
        path = self._workspace / self._QUEUE_FILE
        if not path.is_file():
            return []
        try:
            return [
                str(e["doubt"])
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
                for e in [json.loads(line)]
                if e.get("chapter") == chapter and e.get("doubt")
            ]
        except (OSError, ValueError):
            return []

    def _clear_doubts(self, chapter: str) -> None:
        path = self._workspace / self._QUEUE_FILE
        if not path.is_file():
            return
        try:
            entries = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            rest = [e for e in entries if e.get("chapter") != chapter]
            path.write_text(
                "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in rest),
                encoding="utf-8",
            )
        except (OSError, ValueError):
            pass

    async def _review_chapter(self, chapter: str) -> None:
        """自动审校一章：改动 → apply_review_edit + audit；疑点 → 核验队列；
        模型异常 = infra，留痕并保持未审校（merge 闸会硬失败）。"""
        try:
            text = (self._workspace / "chapters" / chapter).read_text(encoding="utf-8")
            edits, doubts = await with_retry(
                lambda: run_review(text, self._model_access),
                backoff_s=self._verify_backoff,
            )
        except Exception as exc:
            logger.warning("chapter_gate: 第 %s 章审校 infra 失败：%s", chapter, exc)
            self._log_audit({"type": "infra_failure", "chapter": chapter, "error": str(exc)})
            return
        self._enqueue_doubts(chapter, doubts)
        if not edits:
            self.mark_reviewed(chapter)
            return
        new_text, applied, skipped = apply_edits(text, edits)
        if not applied:
            self.mark_reviewed(chapter)
            return
        self.apply_review_edit(chapter, new_text)
        for rec in applied:
            self._log_audit({"type": "edit", "chapter": chapter, **rec})
        for rec in skipped:
            self._log_audit({"type": "skipped", "chapter": chapter, **rec})
        self._auto_reviewed.append((chapter, len(applied)))
        logger.info("chapter_gate: 第 %s 章自动审校改 %d 处", chapter, len(applied))

    async def _merge_autoreview(self, request: ToolCallRequest) -> None:
        """merge 闸前自动审校未审校章（设计 §3.2：harness 自动跑，不等模型）。"""
        if self._model_access is None:
            return
        tool_name, _, _ = _tool_meta(request)
        if tool_name.rsplit("/", 1)[-1] != _MERGE_TOOL:
            return
        for ch in self._all_workspace_chapters():
            st = self._chapter_state(ch)
            if st.reviewed or st.exit_granted:
                continue
            await self._review_chapter(ch)

    @property
    def model_access(self) -> Any | None:
        """核验/审校独立模型通路（设计_R4 §3）；None = 核验/审校工序关闭。"""
        return self._model_access

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
    # 核验（R4 §3.1 章级第五道）
    # ------------------------------------------------------------------

    def _ledger_text(self) -> str:
        p = self._workspace / "data_ledger.md"
        return p.read_text(encoding="utf-8") if p.is_file() else ""

    def _log_verification(self, entry: dict[str, Any]) -> None:
        entry = {"ts": datetime.now(timezone.utc).isoformat(), **entry}
        try:
            with (self._workspace / "verification_log.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            logger.warning("chapter_gate: verification_log.jsonl 写入失败", exc_info=True)

    async def _verify_chapter(self, chapter: str) -> None:
        """核验一章：证伪 → verify_findings（章 dirty）；查无实证 → 降级附言；
        infra 失败 → verified=pending fail-open 留痕，连续 3 次事故中止。"""
        st = self._chapter_state(chapter)
        st.verified = "unchecked"
        st.verify_findings = []
        st.verify_notes = []
        try:
            chapter_text = (self._workspace / "chapters" / chapter).read_text(encoding="utf-8")
            assertions = await with_retry(
                lambda: extract_loadbearing_assertions(
                    chapter_text, self._ledger_text(), self._model_access
                ),
                backoff_s=self._verify_backoff,
            )
            assertions = self._read_doubts(chapter) + assertions  # 审校回流疑点优先核验
            assertions += sample_numeric_claims(chapter_text, assertions, rng=self._rng)
            verdicts = [
                await with_retry(
                    lambda a=a: verify_assertion(a, self._model_access, self._search, self._fetch),
                    backoff_s=self._verify_backoff,
                )
                for a in assertions
            ]
        except Exception as exc:  # infra 失败（重试耗尽）→ 章级 fail-open，不消耗 N=2
            st.verified = "pending"
            self._infra_streak += 1
            logger.warning(
                "chapter_gate: 第 %s 章核验 infra 失败（连续 %d 次）：%s",
                chapter,
                self._infra_streak,
                exc,
            )
            self._log_verification(
                {
                    "type": "infra_failure",
                    "chapter": chapter,
                    "error": str(exc),
                    "streak": self._infra_streak,
                }
            )
            if self._infra_streak >= 3 and not self._verify_aborted:
                self._verify_aborted = True
                logger.error("chapter_gate: 连续 3 次核验 infra 失败，本轮停止核验（事故中止）")
                self._log_verification({"type": "verify_aborted", "streak": self._infra_streak})
            return
        self._infra_streak = 0
        self._clear_doubts(chapter)  # 本轮核验成功，已核疑点出队
        rel = f"chapters/{chapter}"
        for a, v in zip(assertions, verdicts, strict=True):
            self._log_verification(
                {
                    "type": "verdict",
                    "chapter": chapter,
                    "assertion": a,
                    "verdict": v.result,
                    "evidence_url": v.evidence_url,
                    "reason": v.reason,
                }
            )
            if v.result == "refuted":
                st.verify_findings.append(
                    {
                        "file": rel,
                        "line": 0,
                        "check": "verification",
                        "severity": "high",
                        "message": f"承重断言证伪：{a}（证据 {v.evidence_url or '无'}：{v.reason}）",
                    }
                )
            elif v.result == "unverifiable":
                st.verify_notes.append(a)
        st.verified = "failed" if st.verify_findings else "passed"

    async def _verify_touchpoint(
        self,
        request: ToolCallRequest,
        result: ToolMessage | Command[Any],
    ) -> None:
        """report_stats 触点的核验通道：只核验内容检查通过的章（先修格式再核验）。"""
        if self._model_access is None or self._verify_aborted:
            return
        tool_name, _, args = _tool_meta(request)
        if tool_name.rsplit("/", 1)[-1] != _STATS_TOOL or _is_error(result):
            return
        chapter = _chapter_name_from_path(args.get("path"))
        chapters = [chapter] if chapter is not None else self._all_workspace_chapters()
        for ch in chapters:
            if self._verify_aborted:  # 事故中止：目录统计的后续章也跳过
                break
            if self._evaluate_chapter(ch):  # 内容 findings 非空 → 先修格式，核验等下轮复测
                continue
            await self._verify_chapter(ch)

    def _verify_touchpoint_sync(
        self,
        request: ToolCallRequest,
        result: ToolMessage | Command[Any],
    ) -> None:
        """sync 路径的核验入口：无事件循环时 asyncio.run 驱动；失败一律 fail-open。"""
        if self._model_access is None or self._verify_aborted:
            return
        try:
            asyncio.run(self._verify_touchpoint(request, result))
        except Exception:
            logger.warning("chapter_gate: sync 路径核验执行失败，本次跳过（fail-open）", exc_info=True)

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
            target = str(args.get("file_path") or args.get("path") or "")
            # R4 裁定 13：堵 merge 旁路——直接写 report_merged.md 绕过全部成书期工序，硬失败
            if target.replace("\\", "/").endswith("report_merged.md"):
                logger.info("chapter_gate: 拦截 report_merged.md 直接写入（merge 旁路）")
                return ToolMessage(
                    content=(
                        "[chapter_gate] 禁止直接写 report_merged.md（会绕过核验/审校/检查工序）。"
                        "请调用 merge_chapters 由门禁合并生成。"
                    ),
                    name=tool_name or "tool",
                    tool_call_id=call_id,
                    status="error",
                )
            chapter = _chapter_name_from_path(args.get("file_path") or args.get("path"))
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
            # R4 fail-closed（model_access 在位才启用，缺省关闭保持兼容）：
            # 未核验/未审校章硬失败不放行（工序缺位不是内容问题，不消耗 merge_rounds）
            if self._model_access is not None:
                if self._auto_reviewed:
                    # 自动审校已改动 → 阻断本次 merge，附言要求复测（设计 §3.2）
                    notes = self._auto_reviewed
                    self._auto_reviewed = []
                    total = sum(n for _, n in notes)
                    detail = "、".join(f"第 {ch.split('_')[0]} 章 {n} 处" for ch, n in notes)
                    return ToolMessage(
                        content=(
                            f"[chapter_gate] 审校已改 {total} 处（{detail}），"
                            "已落盘并留痕 review_audit.jsonl。请复测各章后再合并。"
                        ),
                        name=tool_name or "tool",
                        tool_call_id=call_id,
                        status="error",
                    )
                hard: list[str] = []
                for ch in self._all_workspace_chapters():
                    st = self._chapter_state(ch)
                    if st.exit_granted:
                        continue  # 诚实出口举证通过 = 合规但记录
                    num = ch.split("_")[0]
                    if st.verified in ("pending", "unchecked"):
                        hard.append(
                            f"第 {num} 章未完成核验"
                            + ("（核验服务不可用，事故中止，本轮建议作废重跑）" if self._verify_aborted else "")
                        )
                    if not st.reviewed:
                        hard.append(f"第 {num} 章未审校")
                if hard:
                    logger.info("chapter_gate: merge 闸硬失败（工序缺位）：%s", hard)
                    return ToolMessage(
                        content="[chapter_gate] 合并闸门（硬失败，不放行）：\n"
                        + "\n".join(f"- {h}" for h in hard),
                        name=tool_name or "tool",
                        tool_call_id=call_id,
                        status="error",
                    )
            findings = self._evaluate_all()
            # 证伪断言（章级第五道产物）并入 merge 全量 findings
            for ch in self._all_workspace_chapters():
                findings.extend(self._chapter_state(ch).verify_findings)
            # 放行条目闭环核销：本次全量复测无 findings 的章，其未闭环条目标闭环
            dirty_files = {f["file"] for f in findings}
            all_files = {f"chapters/{ch}" for ch in self._all_workspace_chapters()}
            self._close_release_items(all_files - dirty_files)
            if not findings:
                return None
            if self._merge_rounds >= self._max:
                logger.warning(
                    "chapter_gate: merge 闸已达自修上限（%d 次），放行合并；"
                    "遗留 findings %d 条交完稿评审",
                    self._merge_rounds,
                    len(findings),
                )
                self._merge_released = findings
                return None
            self._merge_rounds += 1
            logger.info(
                "chapter_gate: 阻断 merge_chapters（%d 条 findings，第 %d 次拦截）",
                len(findings),
                self._merge_rounds,
            )
            recap = self._open_items_recap()
            return ToolMessage(
                content=self._merge_summary(findings) + (f"\n\n{recap}" if recap else ""),
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
                    combined = findings + st.verify_findings  # 内容 + 证伪断言（R4 第五道）
                    st.status = "dirty" if combined else "clean"
                    if not combined:
                        st.repair_rounds = 0  # 复测通过，额度复位
                    results.append((ch, combined))
                ps = (
                    self._chapter_postscript(results[0][1])
                    if chapter is not None
                    else self._multi_postscript(results)
                )
                extra: list[str] = []
                for ch in chapters:
                    st = self._chapters.get(ch)
                    if st is None:
                        continue
                    num = ch.split("_")[0]
                    if st.verify_notes:
                        extra.append(
                            f"第 {num} 章以下断言查无实证，请降级表述强度并标注 [待核实]："
                        )
                        extra.extend(f"- {n}" for n in st.verify_notes[:5])
                    if st.verified == "pending":
                        extra.append(
                            f"第 {num} 章本次核验因核验服务不可用未执行"
                            "（环境事故已留痕，不消耗修复额度）。"
                        )
                if self._verify_aborted:
                    extra.append(
                        "核验服务连续失败已达事故阈值，本轮后续章节跳过核验"
                        "（环境事故，建议本轮作废重跑）。"
                    )
                if extra:
                    ps = ps + "\n" + "\n".join(extra)
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
                st.verified = "unchecked"  # 编辑即清 verified/reviewed（设计裁定 14）
                st.verify_findings = []
                st.verify_notes = []
                st.reviewed = False
            released = self._released
            if released is not None and isinstance(result, ToolMessage):
                first, _ = released
                self._append_release_notes("开新章放行", self._chapter_state(first).findings)
                result = ToolMessage(
                    content=(
                        f"{result.content}\n\n[chapter_gate] 第 {first.split('_')[0]} 章"
                        f"已达自修上限（{self._max} 次），本次放行并记录。"
                        f"{self._RELEASE_TERMINATOR}"
                    ),
                    name=result.name,
                    tool_call_id=result.tool_call_id,
                    status=result.status,
                )
            self._released = None
            return result

        if base == _MERGE_TOOL:
            released_findings = self._merge_released
            if released_findings is not None and isinstance(result, ToolMessage):
                self._append_release_notes("merge 放行", released_findings)
                result = ToolMessage(
                    content=(
                        f"{result.content}\n\n[chapter_gate] 合并闸门已达自修上限"
                        f"（{self._max} 次），本次放行并记录，"
                        f"遗留 {len(released_findings)} 条问题。{self._RELEASE_TERMINATOR}"
                    ),
                    name=result.name,
                    tool_call_id=result.tool_call_id,
                    status=result.status,
                )
            self._merge_released = None
            return result

        return result

    async def _merge_preverify(self, request: ToolCallRequest) -> None:
        """merge 闸 fail-closed 的补核（设计 §3.1）：未核章在 merge 前补核一次。"""
        if self._model_access is None or self._verify_aborted:
            return
        tool_name, _, _ = _tool_meta(request)
        if tool_name.rsplit("/", 1)[-1] != _MERGE_TOOL:
            return
        for ch in self._all_workspace_chapters():
            st = self._chapter_state(ch)
            if st.exit_granted or st.verified in ("passed", "failed"):
                continue
            if self._evaluate_chapter(ch):  # 内容脏章先修格式
                continue
            await self._verify_chapter(ch)

    # ------------------------------------------------------------------
    # wrap_tool_call
    # ------------------------------------------------------------------

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        await self._merge_preverify(request)
        await self._merge_autoreview(request)  # R4 §3.2：先核验（preverify）后审校
        blocked = self._pre_tool_call(request)
        if blocked is not None:
            self._persist_state()
            return blocked
        result = await handler(request)
        await self._verify_touchpoint(request, result)  # R4 第五道：承重断言核验
        out = self._post_tool_call(request, result)
        self._persist_state()
        return out

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        try:
            asyncio.run(self._merge_preverify(request))
            asyncio.run(self._merge_autoreview(request))
        except Exception:
            logger.warning("chapter_gate: sync 路径 merge 补核/审校失败（fail-open）", exc_info=True)
        blocked = self._pre_tool_call(request)
        if blocked is not None:
            self._persist_state()
            return blocked
        result = handler(request)
        self._verify_touchpoint_sync(request, result)
        out = self._post_tool_call(request, result)
        self._persist_state()
        return out


__all__ = [
    "CHAPTER_CHECKERS",
    "MERGE_CHECKERS",
    "ChapterGateMiddleware",
]
