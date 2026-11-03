"""区间解析器（numparse）测试：点值/区间/约数/粒度兼容/冲突判定。"""

from __future__ import annotations

from octop_harness.report_qa.numparse import (
    NumSpan,
    extract_spans,
    parse_span,
    spans_conflict,
    spans_support,
    values_compatible,
)


class TestParseSpan:
    def test_point_value(self) -> None:
        s = parse_span("70.96")
        assert s is not None and s.lo == 70.96 and s.hi == 70.96

    def test_point_with_commas(self) -> None:
        s = parse_span("3,000")
        assert s is not None and s.lo == 3000 and s.hi == 3000

    def test_percent_range(self) -> None:
        s = parse_span("8%~12%")
        assert s is not None and s.lo == 8 and s.hi == 12

    def test_en_dash_range(self) -> None:
        s = parse_span("40–48%")
        assert s is not None and s.lo == 40 and s.hi == 48

    def test_tilde_range(self) -> None:
        s = parse_span("约 5~6 次")
        assert s is not None and s.lo == 5 and s.hi == 6

    def test_zhi_range(self) -> None:
        s = parse_span("3,000 至 3,200 亿元")
        assert s is not None and s.lo == 3000 and s.hi == 3200

    def test_approx_point(self) -> None:
        s = parse_span("~45")
        assert s is not None and s.lo == 45 and s.hi == 45

    def test_unparseable(self) -> None:
        assert parse_span("合并报表") is None

    def test_reversed_range_normalized(self) -> None:
        s = parse_span("12-8")
        assert s is not None and s.lo == 8 and s.hi == 12


class TestSpansConflict:
    def test_disjoint_conflict(self) -> None:
        a = parse_span("8%~12%")
        b = parse_span("15%~18%")
        assert a and b
        assert spans_conflict(a, b)

    def test_boundary_touch_is_conflict(self) -> None:
        """E9 型：8-12 vs 12-14 边界相接 = 矛盾。"""
        a = parse_span("8%~12%")
        b = parse_span("12%~14%")
        assert a and b
        assert spans_conflict(a, b)

    def test_overlap_no_conflict(self) -> None:
        a = parse_span("8%~13%")
        b = parse_span("12%~14%")
        assert a and b
        assert not spans_conflict(a, b)

    def test_point_in_range_no_conflict(self) -> None:
        a = parse_span("13.21%")
        b = parse_span("12%~14%")
        assert a and b
        assert not spans_conflict(a, b)

    def test_point_vs_point_same(self) -> None:
        a = parse_span("70.96")
        b = parse_span("70.96")
        assert a and b
        assert not spans_conflict(a, b)

    def test_point_vs_point_different(self) -> None:
        a = parse_span("70.96")
        b = parse_span("69.70")
        assert a and b
        assert spans_conflict(a, b)


class TestValuesCompatible:
    def test_identical(self) -> None:
        assert values_compatible("70.96", "70.96")

    def test_comma_normalization(self) -> None:
        assert values_compatible("3,000", "3000")

    def test_granularity_substring(self) -> None:
        """粒度差异非冲突：「2016 年」⊂「2016 年 6 月」。"""
        assert values_compatible("2016 年", "2016 年 6 月")
        assert values_compatible("2016 年 6 月", "2016 年")

    def test_numeric_conflict(self) -> None:
        assert not values_compatible("70.96", "69.70")

    def test_range_overlap_compatible(self) -> None:
        assert values_compatible("8%~13%", "12%~14%")

    def test_range_disjoint_incompatible(self) -> None:
        assert not values_compatible("8%~12%", "12%~14%")

    def test_non_numeric_fallback_substring(self) -> None:
        assert values_compatible("上交所主板", "上交所")
        assert not values_compatible("上交所主板", "深交所")


class TestExtractSpans:
    def test_skips_year_tokens(self) -> None:
        """年份是口径不是数值：2021–2025、2024 年 不抽取。"""
        spans = extract_spans("2021–2025 年复合增长率约 30%，2024 年营收 70.96 亿元")
        raws = [s.raw for s in spans]
        assert any("30" in r for r in raws)
        assert any("70.96" in r for r in raws)
        assert not any("2021" in r for r in raws)
        assert not any("2024" == r.strip() for r in raws)

    def test_keeps_non_year_numbers(self) -> None:
        spans = extract_spans("净现金 53–57 亿元，网点 6,000–8,000 家")
        raws = [s.raw for s in spans]
        assert any("53" in r and "57" in r for r in raws)
        assert any("6,000" in r for r in raws)

    def test_excludes_anchor_chapter_number(self) -> None:
        spans = extract_spans("毛利率提升至 39.19%（见第 9 章）")
        raws = [s.raw for s in spans]
        assert any("39.19" in r for r in raws)
        assert not any(r.strip() == "9" for r in raws)


def _span(lo: float, hi: float, raw: str) -> NumSpan:
    return NumSpan(lo, hi, raw)


class TestSpansSupport:
    """conclusion_source 专用的锚点支持判定：比 spans_conflict 严格。"""

    def test_point_exact_hit(self) -> None:
        assert spans_support(_span(39.19, 39.19, "39.19"), _span(39.19, 39.19, "39.19"))

    def test_point_within_eps(self) -> None:
        assert spans_support(_span(70.96, 70.96, "70.96"), _span(70.9, 70.9, "70.9"))

    def test_point_relative_tolerance(self) -> None:
        """取整转述按相对容差放行：70 ≈ 70.96（2%）。"""
        assert spans_support(_span(70.0, 70.0, "70"), _span(70.96, 70.96, "70.96"))

    def test_point_mismatch(self) -> None:
        assert not spans_support(_span(36.91, 36.91, "36.91"), _span(39.19, 39.19, "39.19"))

    def test_range_overlap(self) -> None:
        assert spans_support(_span(12.0, 14.0, "12%~14%"), _span(13.0, 15.0, "13%~15%"))

    def test_range_adjacent_not_support(self) -> None:
        assert not spans_support(_span(12.0, 14.0, "12%~14%"), _span(8.0, 12.0, "8%~12%"))

    def test_point_in_range_not_support(self) -> None:
        """GD-20 防漏检：区间锚点不能被区间内的无关点值支持。"""
        assert not spans_support(_span(88.0, 99.0, "88%~99%"), _span(90.48, 90.48, "90.48"))

    def test_range_in_range_not_support(self) -> None:
        """GD-19 防漏检：点值锚点不能被无关宽区间支持。"""
        assert not spans_support(_span(36.91, 36.91, "36.91"), _span(30.0, 150.0, "30-150"))

    def test_modifier_ignored_support(self) -> None:
        """修饰语不影响同值判定：「39.19」=「约 39.19%」。"""
        assert spans_support(_span(39.19, 39.19, "39.19"), _span(39.19, 39.19, "约 39.19%"))

# ---------------------------------------------------------------------------
# GD-26 负号盲区（设计文档_R4 §8，调研 R-1）
# ---------------------------------------------------------------------------


class TestNegativeSign:
    def test_negative_point_value(self) -> None:
        from octop_harness.report_qa.numparse import parse_span

        s = parse_span("-0.35%")
        assert s is not None and s.lo == s.hi == -0.35

    def test_positive_sign_point_value(self) -> None:
        from octop_harness.report_qa.numparse import parse_span

        s = parse_span("+0.35%")
        assert s is not None and s.lo == 0.35

    def test_negative_percent_not_equal_positive(self) -> None:
        """GD-26：「-0.35%」与「+0.35%」异号 → 判冲突而非兼容。"""
        from octop_harness.report_qa.numparse import parse_span, values_compatible

        neg = parse_span("-0.35%")
        pos = parse_span("+0.35%")
        assert neg is not None and pos is not None
        assert neg.lo < 0 < pos.lo
        # values_compatible 收原始字符串（内部自解析），异号必须判冲突
        assert not values_compatible("-0.35%", "+0.35%")

    def test_interval_syntax_unaffected(self) -> None:
        """区间连接符不被负号改动误伤：「8%~12%」仍是 (8, 12)。"""
        from octop_harness.report_qa.numparse import parse_span

        s = parse_span("8%~12%")
        assert s is not None and (s.lo, s.hi) == (8.0, 12.0)
