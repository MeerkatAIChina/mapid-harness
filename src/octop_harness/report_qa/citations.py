"""引用解析模块（设计文档_R4 §2.1，顺序编码制 [n]）。

三个职责：
- 正文引用编号提取（``extract_citation_refs``）——只认纯数字方括号，不认 Markdown 链接；
- 章末参考资料节解析（``parse_reference_section``）——认「本章参考资料/参考资料/参考文献」三种标题；
- 台账 ref 列解析（``parse_ledger_refs``）——无 ref 列返回 ``None``（旧宿主豁免信号）。
"""

from __future__ import annotations

import re

# 正文引用编号：[n]，n 为 1-3 位数字；(?!\() 排除 Markdown 链接语法 [文字](url) 的兜底
# （数字链接文本如 [12](url) 也不应被当作引用编号）。
CITATION_REF_RE = re.compile(r"\[(\d{1,3})\](?!\()")

# 章末参考资料节标题（三种变体都认）。
_REFERENCE_HEADING_RE = re.compile(r"^#{1,3}\s*(本章)?参考(资料|文献)\s*$", re.MULTILINE)

# 参考资料条目：[n] 条目文本。
_REFERENCE_ENTRY_RE = re.compile(r"^\[(\d{1,3})\]\s*(.+?)\s*$")

# 待核实标记（unsourced_precise 双样式判定用）。
PENDING_MARK = "[待核实]"


def extract_citation_refs(text: str) -> set[int]:
    """提取正文中全部 `[n]` 引用编号。"""
    return {int(m) for m in CITATION_REF_RE.findall(text)}


def parse_reference_section(chapter_text: str) -> dict[int, str]:
    """解析章末参考资料节，返回 编号 -> 条目文本；节不存在返回空 dict。"""
    lines = chapter_text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if _REFERENCE_HEADING_RE.match(line.strip()):
            start = i + 1
            break
    if start is None:
        return {}
    entries: dict[int, str] = {}
    for line in lines[start:]:
        stripped = line.strip()
        # 下一个标题 = 节结束
        if stripped.startswith("#"):
            break
        m = _REFERENCE_ENTRY_RE.match(stripped)
        if m:
            entries[int(m.group(1))] = m.group(2)
    return entries


def strip_reference_section(chapter_text: str) -> str:
    """移除章末参考资料节（标题到下一个标题或文末），返回正文部分。"""
    m = _REFERENCE_HEADING_RE.search(chapter_text)
    if m is None:
        return chapter_text
    rest = chapter_text[m.end():]
    next_heading = re.search(r"^#{1,3}\s", rest, re.MULTILINE)
    end = m.end() + next_heading.start() if next_heading else len(chapter_text)
    return chapter_text[: m.start()] + chapter_text[end:]


def parse_ledger_refs(ledger_text: str) -> set[int] | None:
    """解析台账 ref 列的已注册编号集合；台账没有 ref 列时返回 None（豁免信号）。

    台账可能有多张表（回归脚本会把伪造表追加在原表后）：任何一张表带 ref 列
    即视为启用注册中心，编号取所有带 ref 列表的并集。
    """
    table_lines = [ln for ln in ledger_text.splitlines() if ln.strip().startswith("|")]
    if not table_lines:
        return None
    refs: set[int] = set()
    found_ref_column = False
    i = 0
    while i < len(table_lines):
        cells = [c.strip() for c in table_lines[i].strip().strip("|").split("|")]
        is_sep = all(set(c) <= {"-", ":"} for c in cells if c)
        if is_sep:
            i += 1
            continue
        # 当前行视作表头候选：下一行是分隔行才确认
        header = cells
        j = i + 1
        if j < len(table_lines):
            next_cells = [c.strip() for c in table_lines[j].strip().strip("|").split("|")]
            if all(set(c) <= {"-", ":"} for c in next_cells if c):
                j += 1  # 跳过分隔行
                if "ref" in header:
                    found_ref_column = True
                    ref_idx = header.index("ref")
                    while j < len(table_lines):
                        row = [c.strip() for c in table_lines[j].strip().strip("|").split("|")]
                        if all(set(c) <= {"-", ":"} for c in row if c):
                            break
                        if ref_idx < len(row) and row[ref_idx].isdigit():
                            refs.add(int(row[ref_idx]))
                        j += 1
                i = j
                continue
        i += 1
    return refs if found_ref_column else None
