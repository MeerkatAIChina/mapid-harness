"""数值/区间表述解析器：numeric_consistency 与 conclusion_source 共用。

职责：把「70.96」「8%~12%」「约 5–6 次」「3,000 至 3,200 亿元」这类表述解析为
数值区间，并提供冲突/兼容判定。年份（口径信息）不作为数值抽取。

口径（设计文档_R3 §3.1/§3.2）：
- 冲突 = 区间不重叠或边界相接（E9 型：8–12% vs 12–14% 判矛盾），容差 eps；
- 粒度兼容 = 规范化文本互为子串（「2016 年」⊂「2016 年 6 月」），非冲突。
"""

from __future__ import annotations

import re
from typing import NamedTuple

# 单个数字（支持千分位与小数）
_NUM = r"\d+(?:,\d{3})*(?:\.\d+)?"
# 区间连接符：- – — ~ ～ 至 到
_SEP = r"(?:-|–|—|~|～|至|到)"
# 年份（19xx/20xx）是口径不是数值
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
# 「（见第 X 章）」出处锚点：锚点内的章号不是数值
_ANCHOR_RE = re.compile(r"[（(]\s*见第\s*\d+\s*章\s*[)）]")

_SPAN_RE = re.compile(rf"({_NUM})\s*[%‰]?\s*{_SEP}\s*({_NUM})\s*[%‰]?|({_NUM})")


class NumSpan(NamedTuple):
    lo: float
    hi: float
    raw: str


def _to_float(token: str) -> float:
    return float(token.replace(",", ""))


def _is_yearish(token: str) -> bool:
    return bool(_YEAR_RE.match(token.strip()))


def parse_span(text: str) -> NumSpan | None:
    """把一段文本解析为一个数值区间；无法解析返回 None。

    点值 → (v, v)；区间 → (min, max)（顺序颠倒自动归一）；
    忽略「约/~/%/单位」等修饰，只取数字骨架。
    """
    m = _SPAN_RE.search(text)
    if not m:
        return None
    if m.group(1) is not None:  # 区间
        a, b = _to_float(m.group(1)), _to_float(m.group(2))
        lo, hi = min(a, b), max(a, b)
        return NumSpan(lo=lo, hi=hi, raw=text.strip())
    v = _to_float(m.group(3))
    return NumSpan(lo=v, hi=v, raw=text.strip())


def spans_conflict(a: NumSpan, b: NumSpan, eps: float = 0.1) -> bool:
    """区间冲突判定：包含关系 → 兼容；否则重叠量 ≤ eps（含边界相接）→ 冲突。

    - 点值相等/点值落在区间内 = 包含 → 兼容；
    - E9 型（8–12% vs 12–14%）：互不包含且重叠 0 ≤ eps → 冲突；
    - 8–13% vs 12–14%：重叠 1.0 > eps → 兼容。
    """
    contains = (a.lo <= b.lo + eps and b.hi <= a.hi + eps) or (
        b.lo <= a.lo + eps and a.hi <= b.hi + eps
    )
    if contains:
        return False
    overlap = min(a.hi, b.hi) - max(a.lo, b.lo)
    return overlap <= eps


def _normalize(text: str) -> str:
    return re.sub(r"[,\s]", "", text)


def spans_support(
    anchor: NumSpan, candidate: NumSpan, eps: float = 0.1, rel: float = 0.02
) -> bool:
    """出处锚点支持判定（conclusion_source 专用，比 spans_conflict 严格）。

    「不冲突」不足以支持一个锚点：结论章区间 [88,99] 会被被引章里任何
    落在区间内的无关点值（90.48）「支持」，结论章点值 36.91 也会被无关
    宽区间（30–150）「支持」（GD-19/GD-20 型漏检）。支持必须是同值证据：

    - 点值 vs 点值：|差| ≤ max(eps, rel × 量级) → 支持（rel 容忍「70 亿」
      对「70.96 亿」这类取整转述）；
    - 区间 vs 区间：真实重叠（重叠量 > eps，边界相接不算）→ 支持；
    - 点值与区间互含 → 不支持（区间锚点必须有区间级证据，反之亦然）。
    """
    a_point = anchor.hi - anchor.lo <= eps
    b_point = candidate.hi - candidate.lo <= eps
    if a_point and b_point:
        tol = max(eps, rel * max(abs(anchor.lo), abs(candidate.lo)))
        return abs(anchor.lo - candidate.lo) <= tol
    if a_point != b_point:
        return False
    overlap = min(anchor.hi, candidate.hi) - max(anchor.lo, candidate.lo)
    return overlap > eps


def values_compatible(a: str, b: str, eps: float = 0.1) -> bool:
    """同口径两数值是否兼容：规范化子串（粒度差异）或数值区间不冲突 → 兼容。"""
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return na == nb
    if na == nb or na in nb or nb in na:
        return True
    sa, sb = parse_span(a), parse_span(b)
    if sa is not None and sb is not None:
        return not spans_conflict(sa, sb, eps=eps)
    return False


def extract_spans(text: str) -> list[NumSpan]:
    """抽取文本中的数值区间列表：剔除出处锚点章号与年份（口径信息）。"""
    cleaned = _ANCHOR_RE.sub("", text)
    spans: list[NumSpan] = []
    for m in _SPAN_RE.finditer(cleaned):
        if m.group(1) is not None:
            t1, t2 = m.group(1), m.group(2)
            if _is_yearish(t1) and _is_yearish(t2):
                continue  # 年份区间（如 2021–2025）是口径
            lo, hi = sorted((_to_float(t1), _to_float(t2)))
            spans.append(NumSpan(lo=lo, hi=hi, raw=m.group(0).strip()))
        else:
            token = m.group(3)
            if _is_yearish(token):
                continue  # 单一年份是口径
            v = _to_float(token)
            spans.append(NumSpan(lo=v, hi=v, raw=token))
    return spans


__all__ = [
    "NumSpan",
    "extract_spans",
    "parse_span",
    "spans_conflict",
    "spans_support",
    "values_compatible",
]
