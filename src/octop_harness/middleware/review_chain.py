"""审校链（设计文档_R4 §3.2）：merge 前文字编辑。

只改文字（语病/重复/衔接/表述强度），不改事实与数据；事实疑点写入核验队列
回流 Task 15，审校不独自判。编辑约束：整行替换、每处 ≤300 字、行号倒序应用
（防行号漂移）、越界行号跳过并留痕。留痕 ``review_audit.jsonl``。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from octop_harness.middleware.verification import DEFAULT_TIMEOUT_S

logger = logging.getLogger(__name__)

MAX_EDIT_CHARS = 300

_REVIEW_SYSTEM = (
    "你是研究报告的审校编辑。只改文字：语病、重复啰嗦、逻辑衔接、表述强度；"
    "不改任何事实、数据、来源标注。每处修改为整行替换，新文本不超过 300 字，"
    "行号以 1 为起始。发现事实疑点（数据存疑、事实待核）不要改，列入 doubts。"
    '输出 JSON：{"edits": [{"line": 3, "new": "替换后的整行", "reason": "一句话理由"}], '
    '"doubts": ["疑点1"]}，无需修改则 edits 为空数组。'
)


@dataclass
class Edit:
    line: int  # 1 起始
    new: str
    reason: str = ""


def parse_review_response(raw: str) -> tuple[list[Edit], list[str]]:
    """解析审校模型输出（容忍围栏/废话；越界与超长编辑在这里先滤掉超长）。"""
    cleaned = re.sub(r"```(?:json)?|```", "", raw).strip()
    start = cleaned.find("{")
    if start < 0:
        raise ValueError(f"审校输出无 JSON：{raw[:120]}")
    data = json.loads(cleaned[start:])
    edits: list[Edit] = []
    for item in data.get("edits", []):
        try:
            line = int(item["line"])
            new = str(item["new"])
        except (KeyError, TypeError, ValueError):
            continue
        if line < 1 or len(new) > MAX_EDIT_CHARS:
            continue
        edits.append(Edit(line=line, new=new, reason=str(item.get("reason", ""))))
    doubts = [str(d) for d in data.get("doubts", [])]
    return edits, doubts


def apply_edits(text: str, edits: list[Edit]) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """行号倒序应用（防漂移）。返回 (新文本, 已应用 audit 记录, 跳过记录)。"""
    lines = text.split("\n")
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for e in sorted(edits, key=lambda x: x.line, reverse=True):
        if e.line > len(lines):
            skipped.append({"line": e.line, "new": e.new, "reason": f"越界（全文 {len(lines)} 行）"})
            continue
        old = lines[e.line - 1]
        lines[e.line - 1] = e.new
        applied.append({"line": e.line, "old": old, "new": e.new, "reason": e.reason})
    applied.reverse()  # audit 按行号正序，便于人读
    return "\n".join(lines), applied, skipped


async def run_review(chapter_text: str, model_access: Any) -> tuple[list[Edit], list[str]]:
    """跑一章审校；模型异常向上抛（调用方按 infra 策略处置）。"""
    raw = await model_access.ainvoke_text(
        f"章节文本（行号以 1 起始）：\n{chapter_text}",
        system=_REVIEW_SYSTEM,
        response_format="json",
        temperature=0,
        timeout_s=DEFAULT_TIMEOUT_S,
    )
    return parse_review_response(raw)


__all__ = ["MAX_EDIT_CHARS", "Edit", "apply_edits", "parse_review_response", "run_review"]
