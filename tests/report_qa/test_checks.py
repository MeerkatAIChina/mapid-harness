"""report_qa 检查器测试：小型合成 workspace，每个检查器至少一个命中用例与一个干净用例。"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from octop_harness.report_qa import checks, report
from octop_harness.report_qa.__main__ import main as cli_main
from octop_harness.report_qa.constants import LEDGER_FIELDS

# ---------------------------------------------------------------------------
# fixtures helpers
# ---------------------------------------------------------------------------

LEDGER_HEADER = "| " + " | ".join(LEDGER_FIELDS) + " |"
LEDGER_SEP = "|" + "---|" * len(LEDGER_FIELDS)

BODY_600 = "这是一段正常的报告正文，用于填充章节体量。" * 60  # >500 非空白字符


def non_ws(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def make_ws(
    tmp_path: Path,
    chapters: dict[str, str] | None = None,
    ledger: str | None = None,
    progress: str | None = None,
    outline: str | None = None,
    report_md: str | None = None,
) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    if chapters is not None:
        ch_dir = ws / "chapters"
        ch_dir.mkdir()
        for name, text in chapters.items():
            (ch_dir / name).write_text(text, encoding="utf-8")
    if ledger is not None:
        (ws / "data_ledger.md").write_text(ledger, encoding="utf-8")
    if progress is not None:
        (ws / "progress.md").write_text(progress, encoding="utf-8")
    if outline is not None:
        (ws / "outline.md").write_text(outline, encoding="utf-8")
    if report_md is not None:
        (ws / "report.md").write_text(report_md, encoding="utf-8")
    return ws


def good_ledger(extra_rows: list[str] | None = None) -> str:
    rows = [
        "| 欧普照明2024年营业收入 | 70.96 | 亿元 | 合并报表 | 年报 | 第1章 |",
        "| 全球LED渗透率 | 75 | % | 全球范围 | 观研天下 | 第2章 |",
    ]
    rows.extend(extra_rows or [])
    return "\n".join(["# 数据台账", "", LEDGER_HEADER, LEDGER_SEP, *rows, ""])


# ---------------------------------------------------------------------------
# 1. degeneration
# ---------------------------------------------------------------------------


def test_degeneration_template_burst_hit(tmp_path: Path) -> None:
    names = [
        "孔子",
        "孟子",
        "老子",
        "庄子",
        "墨子",
        "韩非子",
        "孙子",
        "吴起",
        "孙膑",
        "白起",
        "王翦",
        "李牧",
    ]
    rows = "\n".join(f"| 渠道{n}化 | Channel {n} | 销售渠道的{n}化 |" for n in names)
    ws = make_ws(tmp_path, chapters={"01_甲.md": f"# 附录\n\n{rows}\n"})
    findings = checks.check_degeneration(ws)
    assert any(f["check"] == "degeneration" and "模板" in f["message"] for f in findings)


def test_degeneration_identical_line_repeat_hit(tmp_path: Path) -> None:
    line = "| 渠道刘敬化 | Liu Jing | 销售渠道的刘敬化 |"
    ws = make_ws(tmp_path, chapters={"01_甲.md": "\n".join([line] * 3)})
    findings = checks.check_degeneration(ws)
    assert any("重复" in f["message"] for f in findings)


def test_degeneration_identical_line_in_fence_no_hit(tmp_path: Path) -> None:
    """代码块（ASCII 图）内的重复图例行不是退化（金缺陷回归 ch06 L97 FP）。"""
    legend = "| 欧普 佛山 公牛 三雄 阳光 木林森 雷士 |"
    text = f"```\n{legend}\n{legend}\n{legend}\n```\n"
    ws = make_ws(tmp_path, chapters={"01_甲.md": text})
    findings = checks.check_degeneration(ws)
    assert not any("重复" in f["message"] for f in findings)


def test_degeneration_template_burst_normal_tables_no_hit(tmp_path: Path) -> None:
    """正常密集数据表（对标表/年份表）不是模板爆破（金缺陷回归 ch09 L76 / ch02 L18 FP）。"""
    benchmark = "\n".join(
        [
            "| 企业 | 2024年毛利率 | 2023年毛利率 | 行业平均 | 备注 |",
            "|---|---|---|---|---|",
            "| 欧普照明 | 39.3% | 40.1% | 35.0% | 行业较高 |",
            "| 公牛集团 | 46.5% | 45.2% | 35.0% | 行业最高 |",
            "| 佛山照明 | 25.0% | 26.3% | 35.0% | 行业较低 |",
            "| 三雄极光 | 35.0% | 34.8% | 35.0% | 行业平均 |",
            "| 阳光照明 | 30.0% | 31.2% | 35.0% | 行业较低 |",
            "| 木林森 | 28.0% | 29.5% | 35.0% | 行业较低 |",
            "| 雷士照明 | 35.0% | 36.1% | 35.0% | 行业平均 |",
            "| Yeelight | 40.0% | 39.4% | 35.0% | 行业较高 |",
            "| 飞利浦Hue | 45.0% | 44.7% | 35.0% | 行业最高 |",
            "| 松下照明 | 35.0% | 35.9% | 35.0% | 行业平均 |",
            "",
            "*数据来源：各公司年报*",
        ]
    )
    years = "\n".join(
        ["| 年份 | 市场规模（亿元） | 同比增速 | 阶段特征 |", "|---|---|---|---|"]
        + [
            f"| {y} | ~{v} | {g} | {s} |"
            for y, v, g, s in [
                ("2020", "750", "~9.3%", "复苏增长"),
                ("2021", "820", "9.3%", "持续增长"),
                ("2022", "880", "7.3%", "增速放缓"),
                ("2023", "950", "8.0%", "成熟期特征"),
                ("2024", "1020", "7.4%", "成熟期特征"),
                ("2025E", "1100", "7.8%", "预测值"),
                ("2026E", "1185", "7.7%", "预测值"),
                ("2027E", "1270", "7.2%", "预测值"),
                ("2028E", "1360", "7.1%", "预测值"),
                ("2029E", "1450", "6.6%", "预测值"),
                ("2030E", "1540", "6.2%", "预测值"),
            ]
        ]
    )
    approx = "\n".join(
        ["| 企业 | 门店 | 经销商 | 网点 | 覆盖率 |", "|---|---|---|---|---|"]
        + [
            f"| {name} | ~{a} | ~{b}00 | ~{c} | ~{d}% |"
            for name, a, b, c, d in [
                ("欧普照明", 8, 2, 19, 27),
                ("公牛集团", 12, 8, 24, 31),
                ("佛山照明", 6, 12, 15, 22),
                ("三雄极光", 4, 5, 11, 18),
                ("阳光照明", 5, 9, 13, 20),
                ("木林森", 3, 6, 9, 15),
                ("雷士照明", 7, 4, 17, 25),
                ("Yeelight", 2, 1, 6, 12),
                ("飞利浦Hue", 9, 3, 21, 29),
                ("松下照明", 5, 7, 14, 19),
            ]
        ]
    )
    ws = make_ws(
        tmp_path,
        chapters={"01_对标.md": benchmark, "02_年份.md": years, "03_约数.md": approx},
    )
    findings = checks.check_degeneration(ws)
    assert not any("模板" in f["message"] for f in findings)


def test_degeneration_duplicate_paragraph_hit(tmp_path: Path) -> None:
    para = "本章节系统梳理了行业上下游的关键驱动因素与主要约束条件，供后续章节引用，并作为分析框架的基础贯穿全文。"
    text = f"# 章\n\n{para}\n\n中间隔段。\n\n{para}\n"
    ws = make_ws(tmp_path, chapters={"01_甲.md": text})
    findings = checks.check_degeneration(ws)
    assert any("段落" in f["message"] for f in findings)


def test_degeneration_clean(tmp_path: Path) -> None:
    rows = "\n".join(
        [
            "| 营业收入 | 70.96 | 亿元 | 合并报表口径经审计 |",
            "| 净利润 | 9.03 | 亿元 | 归母净利润口径 |",
            "| 员工人数 | 5761 | 人 | 年末在职人数统计 |",
            "| 专卖店 | 1000 | 家 | 全国范围线下门店 |",
            "| 渗透率 | 75 | % | 全球 LED 照明渗透水平 |",
            "| 出口额 | 474.45 | 亿美元 | 2021 年峰值数据 |",
            "| 研发费用 | 3.06 | 亿元 | 合并报表口径 |",
            "| 市值 | 143.29 | 亿元 | 截至最新交易日收盘 |",
            "| 销售费用 | 12.91 | 亿元 | 合并报表口径 |",
            "| 营业成本 | 43.08 | 亿元 | 合并报表口径 |",
            "| 毛利率 | 39.3 | % | 综合毛利率水平 |",
            "| 海外分公司 | 1 | 家 | 首家设于迪拜 |",
        ]
    )
    ws = make_ws(tmp_path, chapters={"01_甲.md": f"# 章\n\n{rows}\n\n{BODY_600}\n"})
    assert checks.check_degeneration(ws) == []


# ---------------------------------------------------------------------------
# 2. word_count
# ---------------------------------------------------------------------------


def test_word_count_chapter_claim_deviation_hit(tmp_path: Path) -> None:
    chapter_text = BODY_600  # 实测约一千余非空白字符
    progress = "# 进度\n\n| 序号 | 章节名称 | 状态 | 字数 |\n|---|---|---|---|\n| 1 | 甲 | 已完成 | 20,000 |\n"
    ws = make_ws(tmp_path, chapters={"01_甲.md": chapter_text}, progress=progress)
    findings = checks.check_word_count(ws)
    assert any(f["check"] == "word_count" and "GD-13" in f["message"] for f in findings)


def test_word_count_total_claim_deviation_hit(tmp_path: Path) -> None:
    progress = "# 进度\n\n- **章节文件字数：** 50,000 / 100,000\n"
    ws = make_ws(tmp_path, chapters={"01_甲.md": BODY_600}, progress=progress)
    findings = checks.check_word_count(ws)
    assert any("字数" in f["message"] for f in findings)


def test_word_count_clean(tmp_path: Path) -> None:
    measured = non_ws(BODY_600)
    claimed = int(measured * 1.05)  # 偏差 5% < 10%
    progress = (
        "# 进度\n\n| 序号 | 章节名称 | 状态 | 字数 |\n|---|---|---|---|\n"
        f"| 1 | 甲 | 已完成 | {claimed:,} |\n"
        f"\n- **章节文件字数：** {claimed:,}\n"
    )
    ws = make_ws(tmp_path, chapters={"01_甲.md": BODY_600}, progress=progress)
    assert checks.check_word_count(ws) == []


# ---------------------------------------------------------------------------
# 3. ledger_schema
# ---------------------------------------------------------------------------


def test_ledger_schema_free_text_hit(tmp_path: Path) -> None:
    ledger = "# 台账\n\n- 欧普2024营收70.96亿元，来源年报\n- 渗透率75%，来源观研天下\n"
    ws = make_ws(tmp_path, ledger=ledger)
    findings = checks.check_ledger_schema(ws)
    assert any(f["check"] == "ledger_schema" for f in findings)


def test_ledger_schema_missing_file_hit(tmp_path: Path) -> None:
    ws = make_ws(tmp_path)
    assert checks.check_ledger_schema(ws) != []


def test_ledger_schema_clean(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, ledger=good_ledger())
    assert checks.check_ledger_schema(ws) == []


def test_ledger_schema_compound_column_clean(tmp_path: Path) -> None:
    """「口径/年份」复合列名视为合规（v3 提示词的写法，R1 快速轮发现的误报）。"""
    text = good_ledger().replace("| 口径 |", "| 口径/年份 |")
    ws = make_ws(tmp_path, ledger=text)
    assert checks.check_ledger_schema(ws) == []


# ---------------------------------------------------------------------------
# 4. ledger_freshness
# ---------------------------------------------------------------------------


def test_ledger_freshness_stale_hit(tmp_path: Path) -> None:
    chapters = {
        "01_甲.md": BODY_600,
        "02_乙.md": "木林森在通用照明市场的份额为2.66%，两家合计约3.4%。来源：年报。" * 5,
    }
    ledger = good_ledger()  # 删掉第2章条目制造台账停更
    ledger = ledger.replace("| 全球LED渗透率 | 75 | % | 全球范围 | 观研天下 | 第2章 |\n", "")
    ws = make_ws(tmp_path, chapters=chapters, ledger=ledger)
    findings = checks.check_ledger_freshness(ws)
    assert any("台账停更" in f["message"] and "02_乙.md" in f["file"] for f in findings)


def test_ledger_freshness_clean(tmp_path: Path) -> None:
    chapters = {
        "01_甲.md": "欧普照明2024年营收70.96亿元，来源：年报。" + BODY_600,
        "02_乙.md": "市场份额为2.66%，来源：年报。" + BODY_600,
    }
    ws = make_ws(tmp_path, chapters=chapters, ledger=good_ledger())
    assert checks.check_ledger_freshness(ws) == []


def test_ledger_freshness_skips_non_data_lines(tmp_path: Path) -> None:
    """触发线跳过字数声明与 [待核实] 行（R1 快速轮发现的两类误触发）。"""
    chapters = {
        # 唯一精确值在字数声明里 → 不触发
        "03_丙.md": "# 章\n\n> 本章目标：约 7,000 字。系统分析。\n" + BODY_600,
        # 唯一精确值在 [待核实] 行里 → 不触发
        "05_丁.md": "# 章\n\n| 1 | 专利数量 | 第3章 | 2,000+ 项 [待核实] |\n" + BODY_600,
    }
    ws = make_ws(tmp_path, chapters=chapters, ledger=good_ledger())
    assert checks.check_ledger_freshness(ws) == []


def test_ledger_freshness_appendix_exempt(tmp_path: Path) -> None:
    """附录章结构性豁免 freshness：待核实清单是不带标记的二手汇总（R1 快速轮发现）。"""
    chapters = {
        "05_附录.md": (
            "# 附录\n\n## 待核实数据清单\n\n"
            "| 序号 | 数据项 | 当前值 |\n|---|---|---|\n"
            "| 1 | 欧普照明累计专利 | 2,000+ 项 |\n"
            "| 2 | 雷士照明营收增速 | 3.57% |\n"
        )
        + BODY_600,
    }
    ws = make_ws(tmp_path, chapters=chapters, ledger=good_ledger())
    assert checks.check_ledger_freshness(ws) == []


# ---------------------------------------------------------------------------
# 5. unsourced_precise
# ---------------------------------------------------------------------------


def test_unsourced_precise_hit(tmp_path: Path) -> None:
    text = "# 章\n\n木林森在通用照明市场的份额为2.66%，得邦照明为0.72%。\n\n两家合计约3.4%。\n"
    ws = make_ws(tmp_path, chapters={"01_甲.md": text})
    findings = checks.check_unsourced_precise(ws)
    assert findings
    assert all(f["severity"] == "high" for f in findings)
    assert any("2.66%" in f["evidence"] for f in findings)


def test_unsourced_precise_clean_with_source(tmp_path: Path) -> None:
    text = "# 章\n\n木林森市场份额为2.66%。\n数据来源：公司2024年年报。\n"
    ws = make_ws(tmp_path, chapters={"01_甲.md": text})
    assert checks.check_unsourced_precise(ws) == []


def test_unsourced_precise_clean_with_todo_mark(tmp_path: Path) -> None:
    text = "# 章\n\n出口额降至388.5亿美元[待核实]，较峰值回落。\n"
    ws = make_ws(tmp_path, chapters={"01_甲.md": text})
    assert checks.check_unsourced_precise(ws) == []


def test_unsourced_precise_word_count_decl_no_hit(tmp_path: Path) -> None:
    """「约 7,000 字」是字数声明不是数据点（R1 快速轮发现的 FP 类）。"""
    text = "# 章\n\n> 本章目标：约 7,000 字。涵盖市场规模与增速。\n"
    ws = make_ws(tmp_path, chapters={"01_甲.md": text})
    assert checks.check_unsourced_precise(ws) == []


# ---------------------------------------------------------------------------
# 6. numeric_consistency
# ---------------------------------------------------------------------------


def test_numeric_consistency_conflict_hit(tmp_path: Path) -> None:
    conflict_row = "| 欧普照明2024年营业收入 | 77.95 | 亿元 | 合并报表 | 年报 | 第3章 |"
    ledger = good_ledger(extra_rows=[conflict_row])
    ws = make_ws(tmp_path, ledger=ledger)
    findings = checks.check_numeric_consistency(ws)
    hit = [f for f in findings if "冲突" in f["message"]]
    assert any("欧普照明2024年营业收入" in f["message"] for f in hit)


def test_numeric_consistency_clean(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, ledger=good_ledger())
    assert checks.check_numeric_consistency(ws) == []


def test_numeric_consistency_free_text_ledger_no_crash(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, ledger="# 台账\n\n随便写写，没有表格。\n")
    assert checks.check_numeric_consistency(ws) == []


# ---------------------------------------------------------------------------
# 7. figure_companion
# ---------------------------------------------------------------------------


def test_figure_companion_hit(tmp_path: Path) -> None:
    text = "# 章\n\n| 企业 | 营收 |\n|---|---|\n| 欧普 | 70.96 |\n\n# 下一节\n"
    ws = make_ws(tmp_path, chapters={"01_甲.md": text})
    findings = checks.check_figure_companion(ws)
    assert any(f["check"] == "figure_companion" for f in findings)


def test_figure_companion_clean(tmp_path: Path) -> None:
    text = (
        "# 章\n\n| 企业 | 营收 |\n|---|---|\n| 欧普 | 70.96 |\n\n"
        "数据来源：公司年报。上表显示欧普营收规模位居行业前列。\n"
    )
    ws = make_ws(tmp_path, chapters={"01_甲.md": text})
    assert checks.check_figure_companion(ws) == []


# ---------------------------------------------------------------------------
# 8. structure
# ---------------------------------------------------------------------------

TODAY = date.today().isoformat()


def _full_chapters() -> dict[str, str]:
    return {"01_甲.md": BODY_600, "02_乙.md": BODY_600 + "差异化乙章内容。"}


def test_structure_numbering_gap_hit(tmp_path: Path) -> None:
    ws = make_ws(
        tmp_path,
        chapters={"01_甲.md": BODY_600, "03_丙.md": BODY_600},
        progress=f"开始日期：{TODAY}\n",
    )
    findings = checks.check_structure(ws)
    assert any("不连续" in f["message"] or "缺失" in f["message"] for f in findings)


def test_structure_shell_chapter_hit(tmp_path: Path) -> None:
    ws = make_ws(
        tmp_path,
        chapters={"01_甲.md": BODY_600, "02_乙.md": "# 乙章\n\n TODO\n"},
        progress=f"开始日期：{TODAY}\n",
    )
    findings = checks.check_structure(ws)
    assert any("空壳" in f["message"] and "02_乙.md" in f["file"] for f in findings)


def test_structure_stale_date_hit(tmp_path: Path) -> None:
    ws = make_ws(
        tmp_path,
        chapters=_full_chapters(),
        progress="# 进度\n\n- **开始日期：** 2025-07-09\n- **完成日期：** 2025-07-09\n",
    )
    findings = checks.check_structure(ws)
    assert any("日期" in f["message"] and "GD-16" in f["message"] for f in findings)


def test_structure_cross_chapter_dup_hit(tmp_path: Path) -> None:
    para = "本段为跨章逐字复制的样板文字，长度必须超过五十个字符才能被检测到，故再补充若干字以确保段落总长度足够。"
    ws = make_ws(
        tmp_path,
        chapters={"01_甲.md": f"# 甲\n\n{para}\n", "02_乙.md": f"# 乙\n\n{para}\n"},
        progress=f"开始日期：{TODAY}\n",
    )
    findings = checks.check_structure(ws)
    assert any("跨章" in f["message"] for f in findings)


def test_structure_clean(tmp_path: Path) -> None:
    ws = make_ws(
        tmp_path,
        chapters=_full_chapters(),
        progress=f"# 进度\n\n- **开始日期：** {TODAY}\n",
    )
    assert checks.check_structure(ws) == []


# ---------------------------------------------------------------------------
# report 聚合 + CLI
# ---------------------------------------------------------------------------


def test_build_report_aggregates(tmp_path: Path) -> None:
    ws = make_ws(
        tmp_path,
        chapters={"01_甲.md": "# 章\n\n市场份额为2.66%，口径未注明。\n"},
        ledger="# 自由文本台账\n\n没有结构化表格。\n",
        progress="开始日期：2025-07-09\n",
    )
    result = report.build_report(ws)
    summary = result["summary"]
    assert summary["total_findings"] == len(result["findings"])
    assert sum(summary["by_check"].values()) == summary["total_findings"]
    assert sum(summary["by_severity"].values()) == summary["total_findings"]
    # 无来源精确数据应进入高危清单
    assert result["high_risk_data"]
    assert all("evidence" in item for item in result["high_risk_data"])


def test_cli_writes_json(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    ws = make_ws(tmp_path, chapters={"01_甲.md": BODY_600}, progress=f"开始日期：{TODAY}\n")
    out = tmp_path / "out.json"
    rc = cli_main([str(ws), "--json", str(out)])
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "summary" in data and "findings" in data
    stdout = capsys.readouterr().out
    assert "findings" in stdout or "发现" in stdout


# ---------------------------------------------------------------------------
# 6b. numeric_consistency（M1 修正：分组键含口径/年份 + 粒度兼容）
# ---------------------------------------------------------------------------


def _numeric_ledger(rows: list[str]) -> str:
    return "\n".join(["# 数据台账", "", LEDGER_HEADER, LEDGER_SEP, *rows, ""])


def test_numeric_multi_year_rows_clean(tmp_path: Path) -> None:
    """M1：同指标多年份分行（口径/年份不同）= 正常台账，不报（round-3 误报型）。"""
    ws = make_ws(
        tmp_path,
        ledger=_numeric_ledger(
            [
                "| 欧普照明营业收入 | 70.96 | 亿元 | 2024年报 | 年报 | 03 |",
                "| 欧普照明营业收入 | 69.70 | 亿元 | 2025年报 | 年报 | 03 |",
                "| 欧普照明营业收入 | 77.95 | 亿元 | 2023年报 | 年报 | 03 |",
            ]
        ),
    )
    findings = checks.check_numeric_consistency(ws)
    assert findings == []


def test_numeric_same_caliber_conflict_hit(tmp_path: Path) -> None:
    """M1：同指标同口径两个不同数值 = 真冲突，应报。"""
    ws = make_ws(
        tmp_path,
        ledger=_numeric_ledger(
            [
                "| 欧普照明营业收入 | 70.96 | 亿元 | 2024年报 | 年报 | 03 |",
                "| 欧普照明营业收入 | 79.06 | 亿元 | 2024年报 | 年报 | 03 |",
            ]
        ),
    )
    findings = checks.check_numeric_consistency(ws)
    assert any(f["check"] == "numeric_consistency" for f in findings)


def test_numeric_granularity_substring_clean(tmp_path: Path) -> None:
    """M1：同口径粒度差异（「2016 年」⊂「2016 年 6 月」）不报。"""
    ws = make_ws(
        tmp_path,
        ledger=_numeric_ledger(
            [
                "| 欧普照明上市时间 | 2016 年 6 月 | — | — | 招股说明书 | 03 |",
                "| 欧普照明上市时间 | 2016 年 | — | — | 招股说明书 | 03 |",
            ]
        ),
    )
    findings = checks.check_numeric_consistency(ws)
    assert findings == []


def test_numeric_caliber_annotation_conflict_hit(tmp_path: Path) -> None:
    """GD-09 型：口径带实算注释（「2024年报」⊂「2024年报实算（…）」）仍算同口径，应报。"""
    ws = make_ws(
        tmp_path,
        ledger=_numeric_ledger(
            [
                "| 木林森2024营收同比增速 | -0.35% | % | 2024年报 | 木林森2024年报 | 05 |",
                "| 木林森2024营收同比增速 | -3.57% | % | 2024年报实算（175.36→169.10亿） | 木林森2024年报 | 09 |",
            ]
        ),
    )
    findings = checks.check_numeric_consistency(ws)
    assert any(f["check"] == "numeric_consistency" for f in findings)


# ---------------------------------------------------------------------------
# 11. conclusion_source（M3：结论章出处锚点核对，内含区间比对）
# ---------------------------------------------------------------------------

CONCLUSION_CH = "12_结论与战略建议.md"


def test_conclusion_source_clean(tmp_path: Path) -> None:
    ws = make_ws(
        tmp_path,
        chapters={
            "03_剖析.md": "欧普照明 2024 年报营收 70.96 亿元，毛利率 39.16%。",
            CONCLUSION_CH: "营收 70.96 亿元（见第 3 章），毛利率 39.16%（见第 3 章）。",
        },
    )
    findings = checks.check_conclusion_source(ws)
    assert findings == []


def test_conclusion_source_tampered_hit(tmp_path: Path) -> None:
    """GD-19：结论章数字与前文不一致（39.16 → 36.91）应报。"""
    ws = make_ws(
        tmp_path,
        chapters={
            "03_剖析.md": "欧普照明 2024 年报毛利率 39.16%。",
            CONCLUSION_CH: "毛利率 36.91%（见第 3 章）。",
        },
    )
    findings = checks.check_conclusion_source(ws)
    assert any(f["check"] == "conclusion_source" and "36.91" in f["message"] for f in findings)


def test_conclusion_source_adjacent_range_hit(tmp_path: Path) -> None:
    """GD-20/E9：区间边界相接（12–14 vs 8–12）判矛盾，应报。"""
    ws = make_ws(
        tmp_path,
        chapters={
            "03_剖析.md": "净利率维持在 8%~12% 区间。",
            CONCLUSION_CH: "净利率 12%~14%（见第 3 章）。",
        },
    )
    findings = checks.check_conclusion_source(ws)
    assert any(f["check"] == "conclusion_source" for f in findings)


def test_conclusion_source_point_in_range_hit(tmp_path: Path) -> None:
    """结论章区间不能被被引章里落在区间内的无关点值「支持」（GD-20 防漏检）。"""
    ws = make_ws(
        tmp_path,
        chapters={
            "09_财务.md": "2025 年净利率 13.21%。",
            CONCLUSION_CH: "净利率约 12%~14%（见第 9 章）。",
        },
    )
    findings = checks.check_conclusion_source(ws)
    assert any(f["check"] == "conclusion_source" for f in findings)


def test_conclusion_source_overlapping_range_clean(tmp_path: Path) -> None:
    """结论章区间与被引章区间真实重叠（12–14 vs 13–15）→ 同值证据，不报。"""
    ws = make_ws(
        tmp_path,
        chapters={
            "09_财务.md": "净利率维持在 13%~15% 区间。",
            CONCLUSION_CH: "净利率约 12%~14%（见第 9 章）。",
        },
    )
    findings = checks.check_conclusion_source(ws)
    assert findings == []


def test_conclusion_source_no_anchor_clean(tmp_path: Path) -> None:
    ws = make_ws(
        tmp_path,
        chapters={CONCLUSION_CH: "本章没有任何出处锚点，数字 123 也不管。"},
    )
    assert checks.check_conclusion_source(ws) == []


def test_conclusion_source_missing_chapter_hit(tmp_path: Path) -> None:
    ws = make_ws(
        tmp_path,
        chapters={CONCLUSION_CH: "营收 70.96 亿元（见第 7 章）。"},
    )
    findings = checks.check_conclusion_source(ws)
    assert any("不存在" in f["message"] or "第 7 章" in f["message"] for f in findings)


# ---------------------------------------------------------------------------
# 10. 引用三级判定（设计文档_R4 §2.2，Task 3）
# ---------------------------------------------------------------------------

LEDGER_WITH_REF = "\n".join(
    [
        "# 数据台账",
        "",
        "| 指标名 | 数值 | 单位 | 口径/年份 | 来源 | 首现章节 | ref |",
        "|---|---|---|---|---|---|---|",
        "| 欧普照明2024年营业收入 | 70.96 | 亿元 | 合并报表 | 欧普照明2024年年度报告 | 03 | 3 |",
        "",
    ]
)

CH_WITH_REFS = "\n".join(
    [
        "# 03 欧普深度剖析",
        "",
        "欧普照明 2024 年营收 70.96 亿元[3]，同比增长 5.2%[3]。",
        BODY_600,
        "",
        "## 本章参考资料",
        "",
        "[3] 欧普照明《2024年年度报告》，2025，上交所披露平台",
        "",
    ]
)


def test_citation_dangling_flags_missing_entry(tmp_path: Path) -> None:
    # 悬空引用在正文（参考资料节之前）：[7] 在章末无条目
    ch = CH_WITH_REFS.replace(BODY_600, BODY_600 + "\n另一段引用了[7]这个编号但章末没有条目。")
    ws = make_ws(tmp_path, chapters={"03_欧普.md": ch})
    findings = checks.check_citation_dangling(ws)
    assert any(
        f["check"] == "citation_dangling" and f["severity"] == "high" and "[7]" in f["message"] for f in findings
    )


def test_citation_dangling_clean(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, chapters={"03_欧普.md": CH_WITH_REFS})
    assert checks.check_citation_dangling(ws) == []


def test_citation_orphan_flags_uncited_entry(tmp_path: Path) -> None:
    ch = CH_WITH_REFS + "[9] 某券商《照明行业研报》，2024，https://x\n"
    ws = make_ws(tmp_path, chapters={"03_欧普.md": ch})
    findings = checks.check_citation_orphan(ws)
    assert any(
        f["check"] == "citation_orphan" and f["severity"] == "medium" and "[9]" in f["message"] for f in findings
    )


def test_citation_orphan_clean(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, chapters={"03_欧普.md": CH_WITH_REFS})
    assert checks.check_citation_orphan(ws) == []


def test_citation_ledger_ref_flags_unregistered(tmp_path: Path) -> None:
    ch = CH_WITH_REFS.replace("[3]", "[8]")
    ws = make_ws(tmp_path, chapters={"03_欧普.md": ch}, ledger=LEDGER_WITH_REF)
    findings = checks.check_citation_ledger_ref(ws)
    assert any(
        f["check"] == "citation_ledger_ref" and f["severity"] == "high" and "[8]" in f["message"] for f in findings
    )


def test_citation_ledger_ref_skips_legacy_ledger(tmp_path: Path) -> None:
    # 台账无 ref 列（旧宿主）→ 跳过，任何 [n] 都不报
    ws = make_ws(tmp_path, chapters={"03_欧普.md": CH_WITH_REFS}, ledger=good_ledger())
    assert checks.check_citation_ledger_ref(ws) == []


def test_unsourced_precise_accepts_bracket_ref(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, chapters={"03_欧普.md": CH_WITH_REFS})
    assert checks.check_unsourced_precise(ws) == []


def test_unsourced_precise_accepts_legacy_style(tmp_path: Path) -> None:
    # 双样式永久并存：旧式「数据来源：」标注仍合法
    ch = "欧普照明 2024 年营收 70.96 亿元。\n> 数据来源：欧普照明2024年年度报告（2024）\n" + BODY_600
    ws = make_ws(tmp_path, chapters={"03_欧普.md": ch})
    assert checks.check_unsourced_precise(ws) == []


def test_unsourced_precise_still_flags_bare_number(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, chapters={"03_欧普.md": "欧普照明 2024 年营收 70.96 亿元。\n" + BODY_600})
    findings = checks.check_unsourced_precise(ws)
    assert any(f["check"] == "unsourced_precise" for f in findings)


def test_figure_companion_accepts_bracket_ref(tmp_path: Path) -> None:
    ch = "\n".join(
        [
            "# 03 欧普",
            "",
            "| 企业 | 份额 |",
            "|---|---|",
            "| 欧普 | 39.3% |",
            "",
            "> 数据来源：[3]",
            "> 解读：欧普份额领先，支撑其龙头判断。该解读两句以上。",
            "",
            BODY_600,
            "",
            "## 本章参考资料",
            "",
            "[3] CSA《行业白皮书》，2024，https://x",
            "",
        ]
    )
    ws = make_ws(tmp_path, chapters={"03_欧普.md": ch})
    assert checks.check_figure_companion(ws) == []


# ---------------------------------------------------------------------------
# 11. 占位符扫描（设计文档_R4 §5.2，GD-21）
# ---------------------------------------------------------------------------


def test_placeholder_scan_flags_xx(tmp_path: Path) -> None:
    body = "全年装机约XX亿千瓦时，同比增长。\n" + BODY_600
    ws = make_ws(tmp_path, chapters={"03_欧普.md": body})
    findings = checks.check_placeholder_scan(ws)
    assert any(f["check"] == "placeholder_scan" and f["severity"] == "high" for f in findings)


def test_placeholder_scan_flags_todo_words(tmp_path: Path) -> None:
    for i, word in enumerate(("待补充", "待填写", "此处省略", "▢")):
        sub = tmp_path / str(i)
        sub.mkdir()
        ws = make_ws(sub, chapters={"03_欧普.md": f"本节数据{word}。\n" + BODY_600})
        assert checks.check_placeholder_scan(ws), f"未命中占位符：{word}"


def test_placeholder_scan_clean_text(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, chapters={"03_欧普.md": "营收 83.02 亿元，口径清晰。\n" + BODY_600})
    assert checks.check_placeholder_scan(ws) == []


# ---------------------------------------------------------------------------
# 12. 同章反复（设计文档_R4 §5.3，GD-27）
# ---------------------------------------------------------------------------

PARA_A = "公牛集团 2024 年营收 168.6 亿元，毛利率 43.25%，新能源与全屋智能业务增长迅速，渠道优势显著。"
PARA_B = "从财务表现看，公牛集团营收 168.6 亿元、毛利率 43.25% 的水平，说明其电工龙头地位依然稳固。"
PARA_C = "佛山照明 2024 年营收 90.48 亿元，毛利率 18.6%，与公牛集团不在同一量级。"


def test_intra_chapter_flags_same_case_same_numbers(tmp_path: Path) -> None:
    # 非逐字、同主体+同数值组的复述段落对 → medium
    ch = "# 05 竞品\n\n" + PARA_A + "\n\n" + BODY_600 + "\n\n" + PARA_B + "\n"
    ws = make_ws(tmp_path, chapters={"05_竞品.md": ch})
    findings = checks.check_intra_chapter_redundancy(ws)
    assert any(f["check"] == "intra_chapter_redundancy" and f["severity"] == "medium" for f in findings)


def test_intra_chapter_clean_distinct_numbers(tmp_path: Path) -> None:
    # 同主体但数值不同 = 正常多角度论述，不报
    ch = "# 05 竞品\n\n" + PARA_A + "\n\n" + BODY_600 + "\n\n" + PARA_C + "\n"
    ws = make_ws(tmp_path, chapters={"05_竞品.md": ch})
    assert checks.check_intra_chapter_redundancy(ws) == []


def test_intra_chapter_ignores_cross_chapter(tmp_path: Path) -> None:
    # 跨章复用合法（用户裁定）：同段落出现在两章 → 本检查器不报
    ws = make_ws(tmp_path, chapters={"01_甲.md": PARA_A + "\n", "02_乙.md": PARA_A + "\n"})
    assert checks.check_intra_chapter_redundancy(ws) == []


# ---------------------------------------------------------------------------
# 13. 空壳章编号排除（设计文档_R4 §7.3，Task 19）
# ---------------------------------------------------------------------------


def test_shell_chapter_excluded_from_numbering(tmp_path: Path) -> None:
    # 05 正常 + 06 空壳（41 字符）+ 07 正常 → 报空壳章，且空壳不算数 → 报编号缺失 6
    ws = make_ws(
        tmp_path,
        chapters={
            "05_甲.md": BODY_600,
            "06_乙.md": "第六章待写。",
            "07_丙.md": BODY_600,
        },
    )
    findings = checks.check_structure(ws)
    messages = [f["message"] for f in findings]
    assert any("空壳章" in m for m in messages)
    assert any("缺失：6" in m or "缺失: 6" in m or ("6" in m and "编号" in m) for m in messages)


def test_normal_chapter_counts_in_numbering(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, chapters={"05_甲.md": BODY_600, "06_乙.md": BODY_600, "07_丙.md": BODY_600})
    findings = checks.check_structure(ws)
    assert not any("编号不连续" in f["message"] for f in findings)


# ---------------------------------------------------------------------------
# R4 终审修复 2c：跨章同号异源 + 台账重复取号
# ---------------------------------------------------------------------------

CH_A_REF1 = "\n".join(
    [
        "# 03 章",
        "",
        "正文引用[1]。" + BODY_600,
        "",
        "## 本章参考资料",
        "",
        "[1] 甲公司《2024年年度报告》，2025",
        "",
    ]
)
CH_B_REF1 = "\n".join(
    [
        "# 04 章",
        "",
        "正文引用[1]。" + BODY_600,
        "",
        "## 本章参考资料",
        "",
        "[1] 乙研究院《照明行业年报》，2024",
        "",
    ]
)


def test_citation_cross_chapter_conflict(tmp_path: Path) -> None:
    """终审复现：两章都用 [1] 分别指向甲、乙两份不同年报，检查结果为零违规。"""
    ws = make_ws(tmp_path, chapters={"03_a.md": CH_A_REF1, "04_b.md": CH_B_REF1})
    findings = checks.check_citation_cross_chapter(ws)
    assert any(f["check"] == "citation_cross_chapter" and "[1]" in f["message"] for f in findings)


def test_citation_cross_chapter_same_source_ok(tmp_path: Path) -> None:
    # 两章 [1] 指向同一条目文本 = 合法共享
    ws = make_ws(tmp_path, chapters={"03_a.md": CH_A_REF1, "04_b.md": CH_A_REF1})
    assert checks.check_citation_cross_chapter(ws) == []


def test_ledger_ref_duplicate_flagged(tmp_path: Path) -> None:
    """终审复现：两个不同台账指标重复注册 ref=1，解析后只剩集合 {1}，冲突不可见。"""
    ledger = "\n".join(
        [
            "# 数据台账",
            "",
            "| 指标 | 数值 | ref |",
            "| --- | --- | --- |",
            "| 甲公司2024年营收 | 100 | 1 |",
            "| 乙公司2024年营收 | 200 | 1 |",
            "",
        ]
    )
    ws = make_ws(tmp_path, ledger=ledger)
    findings = checks.check_ledger_ref_duplicate(ws)
    assert any(f["check"] == "ledger_ref_duplicate" and "ref=1" in f["evidence"] for f in findings)


def test_ledger_ref_duplicate_same_indicator_ok(tmp_path: Path) -> None:
    # 同指标同号多行（数值冲突型，归 numeric_consistency 管）≠ 重复取号
    ledger = "\n".join(
        [
            "# 数据台账",
            "",
            "| 指标 | 数值 | ref |",
            "| --- | --- | --- |",
            "| 甲公司2024年营收 | 100 | 1 |",
            "| 甲公司2024年营收 | 120 | 1 |",
            "",
        ]
    )
    ws = make_ws(tmp_path, ledger=ledger)
    assert checks.check_ledger_ref_duplicate(ws) == []


def test_ledger_ref_duplicate_legacy_exempt(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, ledger=good_ledger())  # 无 ref 列 → 豁免
    assert checks.check_ledger_ref_duplicate(ws) == []


def test_citation_cross_chapter_same_source_different_number(tmp_path: Path) -> None:
    """终审复验复现：两章 [1]/[2] 指向同一条目文本（同源异号），
    违反「同一来源全书固定一个号」，修复前返回 []。"""
    ch_c = CH_A_REF1.replace("[1]", "[2]")  # 同一条目文本，不同编号
    ws = make_ws(tmp_path, chapters={"03_a.md": CH_A_REF1, "04_c.md": ch_c})
    findings = checks.check_citation_cross_chapter(ws)
    assert any("同源异号" in f["message"] for f in findings)
