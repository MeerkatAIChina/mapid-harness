"""Meerkat 约定收拢处：所有 Meerkat 特定约定集中为本模块的模块级常量。

不做配置系统；需要调整时直接改这里。
"""

from __future__ import annotations

import re

# --- 数据台账 schema（D5） ------------------------------------------------
LEDGER_FIELDS: list[str] = ["指标名", "数值", "单位", "口径", "来源", "首现章节"]
LEDGER_FILENAME = "data_ledger.md"
PROGRESS_FILENAME = "progress.md"
OUTLINE_FILENAME = "outline.md"
REPORT_FILENAME = "report.md"
CHAPTERS_DIR = "chapters"
APPENDIX_MARK = "附录"  # 附录章文件名标记（freshness 结构性豁免：附录无首现数据）

# --- 硬约束标记 ------------------------------------------------------------
TODO_MARK = "[待核实]"
SOURCE_KEYWORD = "来源"
# 来源署名判定：字面「来源」，或「根据/据/出自/援引 … 报告/年报/…」句式
SOURCE_RE = re.compile(
    r"来源|"
    r"(?:根据|据|出自|援引)[^。；\n]{0,30}"
    r"(?:报告|年报|季报|白皮书|研究院|研究所|统计局|披露|招股书|数据库|官网)"
)

# --- 字数口径（D2）：非空白字符数 ------------------------------------------
WORD_COUNT_TOLERANCE = 0.10  # 自报与实测偏差阈值（GD-13）

# --- 生成退化（D6） ---------------------------------------------------------
TEMPLATE_BURST_MIN_ROWS = 10  # 同一模板表格行连续罗列的最小行数
TEMPLATE_FIXED_RATIO = 0.4  # 某列公共前后缀占平均长度比例 ≥ 此值视为「模板列」
TEMPLATE_MIN_TEMPLATED_COLS = 2  # 至少多少个「模板且多变」列才判定模板爆破
TEMPLATE_MIN_DISTINCT_VARIANTS = 5  # 「多变」的门槛：可变部分取值种类数
TEMPLATE_MIN_PREFIX_CHARS = 3  # 纯 ASCII 模子的最小前缀长度（「Channel 」级；「20」「~」不算）
IDENTICAL_LINE_MIN_REPEATS = 3  # 完全相同行重复次数阈值
IDENTICAL_LINE_MIN_CHARS = 10  # 参与重复统计的行最小非空白字符数
IDENTICAL_LINE_MIN_CJK = 4  # 参与重复统计的行最少汉字数（散文信号；排除 ASCII 图结构行）
# 划线/框线行：ASCII 图与文本示意图的结构行，非内容行
RULE_LINE_RE = re.compile(r"[\s\-─━_═|+┌┐└┘├┤┬┴┼╔╗╚╝╠╣●○■□█▓░▒~*·]+")
# 署名行：重复的「来源：X」标注是正常署名，不是退化
ATTRIBUTION_RE = re.compile(r"^[*_~>\s]*(数据来源|资料来源|来源|注|图注)[:：]")
DUP_PARAGRAPH_MIN_CHARS = 50  # 逐字重复段落的最小非空白字符数

# --- 结构（D8 + GD-16） -----------------------------------------------------
SHELL_CHAPTER_MIN_CHARS = 500  # 空壳章阈值：正文非空白字符数
DATE_SKEW_MAX_DAYS = 30  # 文档日期与当前日期偏差阈值（GD-16）

# --- 无来源精确数据（D1/D5，GD-06） -----------------------------------------
SOURCE_WINDOW_LINES = 2  # 精确数值所在行 ±N 行窗口内寻找来源标注
FIGURE_WINDOW_LINES = 3  # 表格/图题块附近 ±N 行窗口（D7）

# 精确数值：小数百分比（2.66%）、精确到小数的亿/万（70.96亿）、千分位整数（5,761）
PRECISE_VALUE_RE = re.compile(
    r"\d+\.\d+\s*%|"  # 小数百分比
    r"\d+\.\d+\s*亿|"  # 精确到小数的亿
    r"\d+\.\d+\s*万|"  # 精确到小数的万
    r"\d{1,3}(?:,\d{3})+"  # 千分位整数
)

# --- severity ---------------------------------------------------------------
SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"
