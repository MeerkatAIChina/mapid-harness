"""outline/progress 机读解析测试（设计文档_R4 §7.4 / 执行计划 Task 20）。"""

from __future__ import annotations

from octop_harness.report_qa.outline import parse_exit_applications, parse_word_count_floors


def test_outline_floor_parsing() -> None:
    text = """# 大纲

- 第1章 研究背景：≥6,000字
- 第3章 剖析：下限 8000 字
- 04_渠道.md 不少于 7000 字
| 05 | 竞品对标 | 不低于6000字 |
- 第十二章 结论：至少 9000 字
"""
    floors = parse_word_count_floors(text)
    assert floors == {"01": 6000, "03": 8000, "04": 7000, "05": 6000, "12": 9000}


def test_missing_floor_skips_chapter() -> None:
    # outline 无该章下限 → 不出现在 dict（不触发续写判定，Review Focus #5）
    floors = parse_word_count_floors("- 第2章 行业全景（篇幅视资料而定）\n")
    assert floors == {}


def test_exit_application_parsed_from_progress() -> None:
    text = """# 进度

已完成：01、02 章。

## 出口申请

- 章：06_产品线对比.md
- 大纲要点覆盖：要点1→§6.2；要点2→§6.3
- 实际/目标：4,200 / 6,000
- 不足原因：竞品未披露细分数据，公开信息已穷尽

## 其他
"""
    apps = parse_exit_applications(text)
    assert len(apps) == 1
    app = apps[0]
    assert app["chapter"] == "06"
    assert app["actual"] == 4200
    assert app["target"] == 6000
    assert "§6.2" in str(app["coverage"])
    assert "竞品未披露" in str(app["reason"])


def test_exit_application_absent() -> None:
    assert parse_exit_applications("# 进度\n无出口申请段。\n") == []
