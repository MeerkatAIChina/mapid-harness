"""citations.py 引用解析模块测试（设计文档_R4 §2.1 / 执行计划 Task 2）。"""

from octop_harness.report_qa.citations import (
    extract_citation_refs,
    parse_ledger_refs,
    parse_reference_section,
)


def test_extract_refs_ignores_markdown_links():
    text = "营收 39.3%[12]，见[官网](https://example.com) 与 [3]"
    assert extract_citation_refs(text) == {12, 3}


def test_extract_refs_from_table_cells():
    text = "| 企业 | 份额 |\n|---|---|\n| 欧普 | 39.3%[12] |\n| 雷士 | 8.1%[12] |\n"
    assert extract_citation_refs(text) == {12}


def test_parse_reference_section_title_variants():
    for title in ("## 本章参考资料", "## 参考资料", "## 参考文献"):
        text = f"正文[1]\n{title}\n[1] 欧普照明《2024年报》，2025，https://x\n"
        assert parse_reference_section(text) == {1: "欧普照明《2024年报》，2025，https://x"}


def test_parse_reference_section_missing_returns_empty():
    assert parse_reference_section("正文[1]，没有参考资料节\n") == {}


def test_parse_reference_section_multiple_entries():
    text = (
        "正文[1][2]\n## 本章参考资料\n"
        "[1] 欧普照明《2024年报》，2025，https://a\n"
        "[2] CSA《行业白皮书》，2024，https://b\n"
    )
    assert parse_reference_section(text) == {
        1: "欧普照明《2024年报》，2025，https://a",
        2: "CSA《行业白皮书》，2024，https://b",
    }


def test_parse_ledger_refs_none_without_ref_column():
    old = "| 指标 | 数值 | 口径 | 来源 |\n|---|---|---|---|\n| 营收 | 83亿 | 年报 | 年报 |\n"
    assert parse_ledger_refs(old) is None


def test_parse_ledger_refs_multi_table_union():
    # 回归场景：原表无 ref 列 + 追加表带 ref 列 → 启用注册中心，编号取并集
    text = (
        "| 指标 | 数值 | 来源 |\n|---|---|---|\n| 营收 | 83亿 | 年报 |\n\n"
        "| 指标名 | 数值 | 来源 | ref |\n|---|---|---|---|\n"
        "| 营收 | 70.96 | 年报 | 3 |\n| 规模 | 3900 | 白皮书 | 12 |\n"
    )
    assert parse_ledger_refs(text) == {3, 12}


def test_parse_ledger_refs_with_ref_column():
    new = (
        "| 指标名 | 数值 | 单位 | 口径/年份 | 来源 | 首现章节 | ref |\n"
        "|---|---|---|---|---|---|---|\n"
        "| 欧普照明营业收入 | 70.96 | 亿元 | 2024年报 | 欧普照明2024年年度报告 | 03 | 3 |\n"
        "| 行业规模 | 3900 | 亿元 | 2024 | CSA 白皮书 | 02 | 12 |\n"
    )
    assert parse_ledger_refs(new) == {3, 12}
