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
        "孔子", "孟子", "老子", "庄子", "墨子", "韩非子",
        "孙子", "吴起", "孙膑", "白起", "王翦", "李牧",
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
    para = (
        "本章节系统梳理了行业上下游的关键驱动因素与主要约束条件，"
        "供后续章节引用，并作为分析框架的基础贯穿全文。"
    )
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
    progress = (
        "# 进度\n\n| 序号 | 章节名称 | 状态 | 字数 |\n|---|---|---|---|\n"
        "| 1 | 甲 | 已完成 | 20,000 |\n"
    )
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
    para = (
        "本段为跨章逐字复制的样板文字，长度必须超过五十个字符才能被检测到，"
        "故再补充若干字以确保段落总长度足够。"
    )
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
        ledger=_numeric_ledger([
            "| 欧普照明营业收入 | 70.96 | 亿元 | 2024年报 | 年报 | 03 |",
            "| 欧普照明营业收入 | 69.70 | 亿元 | 2025年报 | 年报 | 03 |",
            "| 欧普照明营业收入 | 77.95 | 亿元 | 2023年报 | 年报 | 03 |",
        ]),
    )
    findings = checks.check_numeric_consistency(ws)
    assert findings == []


def test_numeric_same_caliber_conflict_hit(tmp_path: Path) -> None:
    """M1：同指标同口径两个不同数值 = 真冲突，应报。"""
    ws = make_ws(
        tmp_path,
        ledger=_numeric_ledger([
            "| 欧普照明营业收入 | 70.96 | 亿元 | 2024年报 | 年报 | 03 |",
            "| 欧普照明营业收入 | 79.06 | 亿元 | 2024年报 | 年报 | 03 |",
        ]),
    )
    findings = checks.check_numeric_consistency(ws)
    assert any(f["check"] == "numeric_consistency" for f in findings)


def test_numeric_granularity_substring_clean(tmp_path: Path) -> None:
    """M1：同口径粒度差异（「2016 年」⊂「2016 年 6 月」）不报。"""
    ws = make_ws(
        tmp_path,
        ledger=_numeric_ledger([
            "| 欧普照明上市时间 | 2016 年 6 月 | — | — | 招股说明书 | 03 |",
            "| 欧普照明上市时间 | 2016 年 | — | — | 招股说明书 | 03 |",
        ]),
    )
    findings = checks.check_numeric_consistency(ws)
    assert findings == []


def test_numeric_caliber_annotation_conflict_hit(tmp_path: Path) -> None:
    """GD-09 型：口径带实算注释（「2024年报」⊂「2024年报实算（…）」）仍算同口径，应报。"""
    ws = make_ws(
        tmp_path,
        ledger=_numeric_ledger([
            "| 木林森2024营收同比增速 | -0.35% | % | 2024年报 | 木林森2024年报 | 05 |",
            "| 木林森2024营收同比增速 | -3.57% | % | 2024年报实算（175.36→169.10亿） | 木林森2024年报 | 09 |",
        ]),
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
            "03_剖析.md": "欧普照明 2024 年报营收 70.96 亿元，毛利率 39.16%。" ,
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
