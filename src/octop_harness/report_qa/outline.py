"""outline.md / progress.md 机读解析（设计文档_R4 §7.4）。

- ``parse_word_count_floors``：outline.md 的每章非空白字数下限（半结构化契约：
  章引用 + 「≥/不少于/至少/下限/不低于 N 字」同行）。键 = 两位章号（与 chapters/NN_*.md 对应）。
- ``parse_exit_applications``：progress.md 固定标记段「## 出口申请」的举证条目
  （章名 + 大纲要点→小节覆盖映射 + 实际/目标字数 + 不足原因）。
"""

from __future__ import annotations

import re

_CN_NUM = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

_CHAPTER_REF_RE = re.compile(
    r"第\s*([0-9]{1,2}|[零一二三四五六七八九十]{1,3})\s*章|(?<!\d)(\d{1,2})[_、.．\s]"
)
_FLOOR_RE = re.compile(r"(?:≥|>=|不少于|至少|不低于|下限[：:]{0,2})\s*([0-9][0-9,]*)\s*字")


def _cn_to_int(text: str) -> int | None:
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    m = re.fullmatch(r"([一二三四五六七八九])?十([一二三四五六七八九])?", text)
    if m:
        tens = _CN_NUM[m.group(1)] if m.group(1) else 1
        ones = _CN_NUM[m.group(2)] if m.group(2) else 0
        return tens * 10 + ones
    return _CN_NUM.get(text)


def parse_word_count_floors(outline_text: str) -> dict[str, int]:
    """章号（两位字符串）→ 非空白字数下限。无下限的章不出现在 dict（Review Focus #5）。"""
    floors: dict[str, int] = {}
    for line in outline_text.splitlines():
        floor_m = _FLOOR_RE.search(line)
        if not floor_m:
            continue
        ref_m = _CHAPTER_REF_RE.search(line)
        if not ref_m:
            continue
        num = _cn_to_int(ref_m.group(1) or ref_m.group(2))
        if num is None or num <= 0:
            continue
        floors[f"{num:02d}"] = int(floor_m.group(1).replace(",", ""))
    return floors


# ---------------------------------------------------------------------------
# 出口申请（progress.md「## 出口申请」标记段）
# ---------------------------------------------------------------------------

_EXIT_SECTION_RE = re.compile(r"^\s*##\s*出口申请\s*$", re.MULTILINE)
_EXIT_FIELD_RE = re.compile(r"^\s*[-*]?\s*(章|章节|覆盖|大纲要点覆盖|实际/目标|不足原因|原因)[：:]\s*(.+)$")
_EXIT_CHAPTER_RE = re.compile(r"(\d{1,2})")


def parse_exit_applications(progress_text: str) -> list[dict[str, object]]:
    """解析「## 出口申请」段；返回条目列表（chapter 为两位章号，可能指向不存在的章）。"""
    m = _EXIT_SECTION_RE.search(progress_text)
    if not m:
        return []
    section = progress_text[m.end() :]
    next_h2 = re.search(r"^##\s", section, re.MULTILINE)
    if next_h2:
        section = section[: next_h2.start()]

    apps: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for line in section.splitlines():
        fm = _EXIT_FIELD_RE.match(line.strip())
        if not fm:
            continue
        key, value = fm.group(1), fm.group(2).strip()
        if key in ("章", "章节"):
            num_m = _EXIT_CHAPTER_RE.search(value)
            if not num_m:
                continue
            current = {
                "chapter": f"{int(num_m.group(1)):02d}",
                "coverage": "",
                "actual": None,
                "target": None,
                "reason": "",
            }
            apps.append(current)
        elif current is None:
            continue
        elif key in ("覆盖", "大纲要点覆盖"):
            current["coverage"] = value
        elif key == "实际/目标":
            nums = re.findall(r"[0-9][0-9,]*", value)
            if len(nums) >= 2:
                current["actual"] = int(nums[0].replace(",", ""))
                current["target"] = int(nums[1].replace(",", ""))
        elif key in ("不足原因", "原因"):
            current["reason"] = value
    return apps


__all__ = ["parse_exit_applications", "parse_word_count_floors"]
