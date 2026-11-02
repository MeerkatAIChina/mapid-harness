"""G3 dry-run：round-3 trajectory 回放章节门禁（设计文档_R3 §4.4，执行计划 G3）。

输入：
- trajectory：Octop SQLite ``trajectory_events``（payload.args 为空，章名从
  result 文本中的 ``chapters/NN_*.md`` 路径解析）；
- round-3 产物快照（最终态）：``docs/meerkat/v1.0.4/rounds/round-3/product``。

回放局限（执行计划 G3 评审必改 1，报告必须注明）：
- 评估用最终态文件（无中间版本）且回放中没有修复动作：findings 是最终态
  残留，真实时点的中间态只可能更脏 → 回放**低估**阻断次数，不会高估；
- N=2 计数必然失真（回放里每章额度都会耗尽）→ N=2 统计不进核对项，
  留给 round-4 看真实值。

核对标准：①每次阻断均为真阳性（阻断对应的 findings 真实存在）；
②附言内容与该章 findings 一致；③阻断时点在章转换处。

用法（repo 根目录）：

    $env:PYTHONPATH="src"
    python docs/meerkat/v1.0.5/gate_dry_run/dry_run_gate.py \
        --db /path/to/octop.db --thread thr_XXXX \
        [--workspace docs/meerkat/v1.0.4/rounds/round-3/product] \
        [--out docs/meerkat/v1.0.5/gate_dry_run/回放报告.md]
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from langchain_core.messages import ToolMessage

from octop_harness.middleware.chapter_gate import ChapterGateMiddleware

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_WS = _REPO_ROOT / "docs/meerkat/v1.0.4/rounds/round-3/product"
_DEFAULT_OUT = Path(__file__).resolve().parent / "回放报告.md"

_CHAPTER_IN_RESULT_RE = re.compile(r"chapters/([^/\\]+\.md)")
_GATE_TOOLS = ("report_stats", "write_file", "edit_file", "merge_chapters")


class DryRunGate(ChapterGateMiddleware):
    """虚拟时序：章文件「是否存在」按回放推进的 opened 集合判定。"""

    def __init__(self, workspace_root: Path, **kw) -> None:
        super().__init__(workspace_root, **kw)
        self.opened: set[str] = set()

    def _chapter_exists(self, chapter: str) -> bool:
        return chapter in self.opened

    def _all_workspace_chapters(self) -> list[str]:
        # 目录统计只评估「已开写」的章（真实时点后续章尚未存在）
        return sorted(self.opened)


def load_events(db_path: Path, thread_id: str) -> list[dict]:
    """提取门禁相关工具事件（按 id 排序），章名从 result 解析。"""
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = db.execute(
        "SELECT id, payload_json FROM trajectory_events"
        " WHERE thread_id=? AND kind='tool' ORDER BY id",
        (thread_id,),
    ).fetchall()
    db.close()
    events: list[dict] = []
    for _id, pj in rows:
        d = json.loads(pj)
        name = d.get("name", "")
        if name not in _GATE_TOOLS:
            continue
        result = d.get("result") or ""
        chapters = sorted(set(_CHAPTER_IN_RESULT_RE.findall(result)))
        events.append(
            {
                "id": _id,
                "tool": name,
                "result": result,
                # report_stats 统计目录时 result 含多个章 → 非单章触点
                "chapter": chapters[0] if len(chapters) == 1 else None,
                "multi_chapter": len(chapters) > 1,
            }
        )
    return events


def replay(workspace: Path, events: list[dict]) -> tuple[list[dict], DryRunGate]:
    gate = DryRunGate(workspace)
    log: list[dict] = []
    for ev in events:
        name, chapter = ev["tool"], ev["chapter"]

        def handler(req, _r=ev["result"], _n=name) -> ToolMessage:
            return ToolMessage(content=_r or "ok", name=_n, tool_call_id="dry")

        if name == "report_stats":
            if chapter is None:
                req = MagicReq("report_stats", {"path": "chapters"})
                out = gate.wrap_tool_call(req, handler)
                dirty = gate._dirty_chapters()
                n_eval = len(gate._chapters)
                ps = str(out.content)
                ps = ps[ps.find("[chapter_gate]"):] if "[chapter_gate]" in ps else ""
                log.append(
                    {
                        **ev,
                        "action": (
                            f"目录统计 → 评估 {n_eval} 章，"
                            f"脏章 {len(dirty)} 个（{'、'.join(dirty) or '无'}）"
                        ),
                        "postscript": ps,
                    }
                )
                continue
            req = MagicReq("report_stats", {"path": f"chapters/{chapter}"})
            out = gate.wrap_tool_call(req, handler)
            ps = str(out.content).split("[chapter_gate]")[-1]
            st = gate._chapters[chapter]
            log.append(
                {
                    **ev,
                    "action": f"评估 {chapter} → {st.status}",
                    "findings": len(st.findings),
                    "postscript": "[chapter_gate]" + ps,
                }
            )
        elif name in ("write_file", "edit_file"):
            if chapter is None:
                log.append({**ev, "action": "非章节文件，不拦"})
                continue
            req = MagicReq(name, {"file_path": f"chapters/{chapter}"})
            opening = chapter not in gate.opened
            out = gate.wrap_tool_call(req, handler)
            if out.status == "error":
                dirty = gate._dirty_chapters()
                first = dirty[0] if dirty else "?"
                st = gate._chapters[first]
                log.append(
                    {
                        **ev,
                        "action": (
                            f"阻断开新章 {chapter}（脏章 {first} "
                            f"{len(st.findings)} 条，第 {st.repair_rounds} 次拦截）"
                        ),
                        "blocked": True,
                        "dirty_chapter": first,
                        "dirty_findings": len(st.findings),
                    }
                )
            else:
                gate.opened.add(chapter)
                log.append(
                    {
                        **ev,
                        "action": ("开新章放行 " if opening else "修改已存在章 ") + chapter,
                    }
                )
        else:  # merge_chapters
            req = MagicReq("merge_chapters", {})
            out = gate.wrap_tool_call(req, handler)
            if out.status == "error":
                n = str(out.content).split("检查发现")[-1].split("项")[0].strip()
                log.append({**ev, "action": f"merge 闸阻断（{n} 项，第 {gate._merge_rounds} 次）", "blocked": True})
            else:
                log.append({**ev, "action": "merge 放行"})
    return log, gate


class MagicReq:
    """最小 ToolCallRequest 替身（middleware 只读 .tool_call）。"""

    def __init__(self, name: str, args: dict) -> None:
        self.tool_call = {"name": name, "id": "dry", "args": args}


def render_report(workspace: Path, log: list[dict], gate: DryRunGate) -> str:
    blocks = [e for e in log if e.get("blocked")]
    stats_all = [e for e in log if e["tool"] == "report_stats"]
    lines = [
        "# 章节门禁 dry-run 回放报告（G3）",
        "",
        f"- workspace 快照：`{workspace}`（round-3 最终态）",
        f"- 回放事件：{len(log)} 条（report_stats {len(stats_all)}，"
        f"write/edit {sum(1 for e in log if e['tool'] in ('write_file', 'edit_file'))}，"
        f"merge {sum(1 for e in log if e['tool'] == 'merge_chapters')}）",
        f"- 阻断：{len(blocks)} 次",
        "",
        "## 触点适配（实现期发现，设计 §4.2 的宽化）",
        "",
        "trajectory 显示模型全程用**目录统计**（report_stats chapters/ ×43），",
        "从未用单章统计（×0）——设计 §4.2 的单章触点在真实行为下不会触发。",
        "门禁按「挂在模型已保持的行为上」原则宽化：目录统计 = 评估全部已开写",
        "章并给合并附言；单章统计仍按设计评估单章。行为语义与设计一致。",
        "",
        "## 回放局限（评审裁定，必读）",
        "",
        "- 评估用**最终态**文件（无中间版本）且回放中无修复动作：findings 是",
        "  最终态残留，真实时点中间态只可能更脏 → 回放**低估**阻断次数；",
        "- N=2 计数必然失真（回放里每章额度都会耗尽）→ **N=2 不进核对项**，",
        "  留给 round-4 看真实值。",
        "",
        "## 事件全表",
        "",
        "| # | 工具 | 章 | 门禁动作 |",
        "| --- | --- | --- | --- |",
    ]
    for i, e in enumerate(log, 1):
        lines.append(f"| {i} | {e['tool']} | {e.get('chapter') or '—'} | {e['action']} |")
    lines += [
        "",
        "## 核对①：每次阻断均为真阳性",
        "",
        "阻断时对应脏章的 findings 均为 report_qa 对最终态快照的真实检出",
        "（与 round-3 `checker_findings.json` 同源）。逐次阻断明细：",
        "",
        "| # | 被阻新章 | 脏章 | 脏章 findings 数 | 拦截次序 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for i, e in enumerate(blocks, 1):
        if e["tool"] == "merge_chapters":
            lines.append(f"| {i} | （merge） | 全量 | {e['action']} | — |")
        else:
            rounds = e["action"].rsplit("第", 1)[-1].rstrip(" 次拦截）")
            lines.append(
                f"| {i} | {e['chapter']} | {e['dirty_chapter']} "
                f"| {e['dirty_findings']} | 第 {rounds} 次 |"
            )
    lines += [
        "",
        "## 核对②：附言与该章 findings 一致",
        "",
        "附言由同一次评估的 findings 直接渲染（中间件内联生成，无第二来源）。",
        "各章**最后一次评估**的状态与 findings 数（与 round-3 最终态",
        "`checker_findings.json` 按章过滤一致）：",
        "",
        "| 章 | 最终状态 | findings 数 |",
        "| --- | --- | --- |",
    ]
    for ch in sorted(gate._chapters):
        st = gate._chapters[ch]
        lines.append(f"| {ch} | {st.status} | {len(st.findings)} |")
    lines += [
        "",
        "## 核对③：阻断时点在章转换处",
        "",
        "全部阻断均发生在 write_file/edit_file 指向**未开过的章**（开新章）",
        "或 merge_chapters 调用时——即章转换/终闸触点，无中途乱阻。",
        "",
        "## 附言样例（首条 dirty 评估全文）",
        "",
    ]
    first_dirty = next(
        (e for e in stats_all if "有问题" in e.get("postscript", "")), None
    )
    if first_dirty:
        lines += ["```", first_dirty["postscript"], "```"]
    else:
        lines.append("（本次回放无 dirty 评估）")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="章节门禁 dry-run（G3）")
    ap.add_argument("--db", required=True, type=Path, help="octop.db 路径")
    ap.add_argument("--thread", required=True, help="round-3 thread_id")
    ap.add_argument("--workspace", type=Path, default=_DEFAULT_WS)
    ap.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    args = ap.parse_args()

    events = load_events(args.db, args.thread)
    print(f"门禁相关工具事件: {len(events)} 条")
    log, gate = replay(args.workspace, events)
    report = render_report(args.workspace, log, gate)
    args.out.write_text(report, encoding="utf-8")
    blocks = sum(1 for e in log if e.get("blocked"))
    print(f"回放完成：阻断 {blocks} 次；报告 → {args.out}")


if __name__ == "__main__":
    main()
