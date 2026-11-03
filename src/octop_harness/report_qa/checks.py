"""检查器实现：每个检查器接收 workspace 路径，返回 findings 列表。

finding = {check, severity, file, line, message, evidence}；全程离线、只读 workspace。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import TypedDict

from octop_harness.report_qa.numparse import (
    extract_spans,
    spans_support,
    values_compatible,
)
from octop_harness.report_qa.citations import (
    CITATION_REF_RE,
    extract_citation_refs,
    parse_ledger_refs,
    parse_reference_section,
    strip_reference_section,
)
from octop_harness.report_qa.constants import (
    APPENDIX_MARK,
    ATTRIBUTION_RE,
    CHAPTERS_DIR,
    DATE_SKEW_MAX_DAYS,
    DUP_PARAGRAPH_MIN_CHARS,
    FIGURE_WINDOW_LINES,
    IDENTICAL_LINE_MIN_CHARS,
    IDENTICAL_LINE_MIN_CJK,
    IDENTICAL_LINE_MIN_REPEATS,
    LEDGER_FIELDS,
    LEDGER_FILENAME,
    PRECISE_VALUE_RE,
    PROGRESS_FILENAME,
    REPORT_FILENAME,
    RULE_LINE_RE,
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    SHELL_CHAPTER_MIN_CHARS,
    SOURCE_RE,
    SOURCE_WINDOW_LINES,
    TEMPLATE_BURST_MIN_ROWS,
    TEMPLATE_FIXED_RATIO,
    TEMPLATE_MIN_DISTINCT_VARIANTS,
    TEMPLATE_MIN_PREFIX_CHARS,
    TEMPLATE_MIN_TEMPLATED_COLS,
    TODO_MARK,
    WORD_COUNT_TOLERANCE,
)


class Finding(TypedDict):
    check: str
    severity: str
    file: str
    line: int
    message: str
    evidence: str


# ---------------------------------------------------------------------------
# 公共工具
# ---------------------------------------------------------------------------


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _chapter_files(workspace: Path) -> list[tuple[int, Path]]:
    """返回 (章节号, 路径) 列表，按章节号排序；无法解析编号的排最后（编号取 0）。"""
    ch_dir = workspace / CHAPTERS_DIR
    if not ch_dir.is_dir():
        return []
    out: list[tuple[int, Path]] = []
    for p in sorted(ch_dir.glob("*.md")):
        m = re.match(r"(\d+)_", p.name)
        out.append((int(m.group(1)) if m else 0, p))
    out.sort(key=lambda t: (t[0] == 0, t[0]))
    return out


def _rel(workspace: Path, path: Path) -> str:
    return path.relative_to(workspace).as_posix()


def _non_ws(text: str) -> int:
    """字数口径：非空白字符数。"""
    return len(re.sub(r"\s+", "", text))


def _hanzi(text: str) -> int:
    return len(re.findall(r"[一-鿿]", text))


def _is_table_line(line: str) -> bool:
    # 排除 ASCII 图的坐标轴/标尺行（如 ``|______|______|``）：以 | 开头但纯规则字符
    return line.strip().startswith("|") and not re.fullmatch(r"[\s|:\-_]+", line)


def _is_separator_row(line: str) -> bool:
    return bool(re.fullmatch(r"[\s|:\-]+", line)) and "-" in line


def _fence_ranges(lines: list[str]) -> list[tuple[int, int]]:
    """``` 代码块的 (start, end) 0-based 闭区间列表（ASCII 图/文本示意图住在里面）。"""
    ranges: list[tuple[int, int]] = []
    start: int | None = None
    for i, line in enumerate(lines):
        if line.strip().startswith("```"):
            if start is None:
                start = i
            else:
                ranges.append((start, i))
                start = None
    if start is not None:
        ranges.append((start, len(lines) - 1))
    return ranges


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _paragraphs(text: str) -> list[tuple[int, str]]:
    """按空行切段，返回 (起始行号, 压缩空白后的段落)。"""
    out: list[tuple[int, str]] = []
    start: int | None = None
    buf: list[str] = []
    for idx, line in enumerate(text.splitlines(), start=1):
        if line.strip():
            if start is None:
                start = idx
            buf.append(line.strip())
        elif buf:
            out.append((start or idx, re.sub(r"\s+", "", "".join(buf))))
            buf = []
            start = None
    if buf:
        out.append((start or 1, re.sub(r"\s+", "", "".join(buf))))
    return out


def _finding(
    check: str,
    severity: str,
    file: str,
    line: int,
    message: str,
    evidence: str,
) -> Finding:
    return {
        "check": check,
        "severity": severity,
        "file": file,
        "line": line,
        "message": message,
        "evidence": evidence[:200],
    }


def _parse_ledger_tables(
    text: str,
) -> list[tuple[int, list[str], list[tuple[int, list[str]]]]]:
    """解析 markdown 表格，返回 (表头行号, 表头列, [(数据行号, 行 cells)]) 列表。"""
    lines = text.splitlines()
    tables: list[tuple[int, list[str], list[tuple[int, list[str]]]]] = []
    i = 0
    while i < len(lines) - 1:
        if _is_table_line(lines[i]) and _is_separator_row(lines[i + 1]):
            header = _split_row(lines[i])
            rows: list[tuple[int, list[str]]] = []
            j = i + 2
            while j < len(lines) and _is_table_line(lines[j]):
                rows.append((j + 1, _split_row(lines[j])))
                j += 1
            tables.append((i + 1, header, rows))
            i = j
        else:
            i += 1
    return tables


# ---------------------------------------------------------------------------
# 1. degeneration（D6 生成退化）
# ---------------------------------------------------------------------------


def _common_prefix_len(values: list[str]) -> int:
    if not values:
        return 0
    prefix = values[0]
    for v in values[1:]:
        while not v.startswith(prefix):
            prefix = prefix[:-1]
            if not prefix:
                return 0
    return len(prefix)


def _common_suffix_len(values: list[str]) -> int:
    if not values:
        return 0
    suffix = values[0]
    for v in values[1:]:
        while not v.endswith(suffix):
            suffix = suffix[1:]
            if not suffix:
                return 0
    return len(suffix)


def _window_is_template_burst(rows: list[list[str]]) -> bool:
    """判断一组同宽表格行是否构成模板爆破：≥2 个「模板且多变」列。

    「模板且多变」= 公共前后缀占比高（同一模子）且可变部分取值多样（≥5 种），
    且模子是实质前缀：生成式退化以前缀驱动（「渠道」X化、「Channel 」X、
    「销售渠道的」X化），前缀须含汉字或 ≥3 字符。「~」「%」「20」「次」
    这类装饰性前后缀不算模子，否则年份列/约数列/百分比列所在的正常数据表
    会误报（金缺陷回归第三类 FP）。
    """
    width = len(rows[0])
    burst_cols = 0
    for col in range(width):
        cells = [r[col] for r in rows]
        avg_len = sum(len(c) for c in cells) / len(cells)
        if avg_len == 0:
            continue
        prefix_len = _common_prefix_len(cells)
        suffix_len = _common_suffix_len(cells)
        if (prefix_len + suffix_len) / avg_len < TEMPLATE_FIXED_RATIO:
            continue
        prefix = cells[0][:prefix_len]
        if not re.search(r"[一-鿿]", prefix) and prefix_len < TEMPLATE_MIN_PREFIX_CHARS:
            continue
        variants = {c[prefix_len : len(c) - suffix_len or None] for c in cells}
        if len(variants) >= TEMPLATE_MIN_DISTINCT_VARIANTS:
            burst_cols += 1
    return burst_cols >= TEMPLATE_MIN_TEMPLATED_COLS


def _check_template_burst(workspace: Path, path: Path, lines: list[str]) -> list[Finding]:
    # 模板爆破常表现为大表内部的连续子序列（如术语表中的「渠道X化」），
    # 因此用滑动窗口在表块内找连续同模板的行段，而非对整表做判定。
    findings: list[Finding] = []
    i = 0
    while i < len(lines):
        if not _is_table_line(lines[i]):
            i += 1
            continue
        block: list[tuple[int, list[str]]] = []
        while i < len(lines) and (_is_table_line(lines[i]) or _is_separator_row(lines[i])):
            if not _is_separator_row(lines[i]):
                block.append((i + 1, _split_row(lines[i])))
            i += 1
        width = max((len(r) for _, r in block), default=0)
        rows = [(ln, r) for ln, r in block if len(r) == width]
        if width == 0 or len(rows) < TEMPLATE_BURST_MIN_ROWS:
            continue
        burst_at: int | None = None
        for w in range(len(rows) - TEMPLATE_BURST_MIN_ROWS + 1):
            window = [r for _, r in rows[w : w + TEMPLATE_BURST_MIN_ROWS]]
            if _window_is_template_burst(window):
                burst_at = rows[w][0]
                break
        if burst_at is not None:
            findings.append(
                _finding(
                    "degeneration",
                    SEVERITY_HIGH,
                    _rel(workspace, path),
                    burst_at,
                    f"检测到模板爆破：表块内第 {burst_at} 行起连续 "
                    f"{TEMPLATE_BURST_MIN_ROWS}+ 行表格仅局部后缀不同",
                    lines[burst_at - 1].strip(),
                )
            )
    return findings


def _check_identical_lines(workspace: Path, path: Path, lines: list[str]) -> list[Finding]:
    """完全相同行重复。表格行只排除表头（后接分隔行）——数据行逐字重复正是 GD-11
    的退化形态；框线/署名/短汉字行/代码块（ASCII 图）内部行不是退化。"""
    fence_lines: set[int] = set()
    for s, e in _fence_ranges(lines):
        fence_lines.update(range(s, e + 1))
    counts: dict[str, list[int]] = {}
    for idx, line in enumerate(lines, start=1):
        if idx - 1 in fence_lines:
            continue
        norm = re.sub(r"\s+", "", line)
        is_table_header = (
            _is_table_line(line) and idx < len(lines) and _is_separator_row(lines[idx])
        )
        if (
            len(norm) < IDENTICAL_LINE_MIN_CHARS
            or _is_separator_row(line)
            or is_table_header
            or RULE_LINE_RE.fullmatch(line)
            or ATTRIBUTION_RE.match(line)
            or len(re.findall(r"[一-鿿]", line)) < IDENTICAL_LINE_MIN_CJK
        ):
            continue
        counts.setdefault(norm, []).append(idx)
    findings: list[Finding] = []
    for norm, linenos in counts.items():
        if len(linenos) >= IDENTICAL_LINE_MIN_REPEATS:
            findings.append(
                _finding(
                    "degeneration",
                    SEVERITY_HIGH,
                    _rel(workspace, path),
                    linenos[0],
                    f"完全相同行重复 {len(linenos)} 次（行 {linenos[:5]}）",
                    norm,
                )
            )
    return findings


def _check_dup_paragraphs(workspace: Path, path: Path, text: str) -> list[Finding]:
    seen: dict[str, list[int]] = {}
    for start, para in _paragraphs(text):
        if len(para) >= DUP_PARAGRAPH_MIN_CHARS:
            seen.setdefault(para, []).append(start)
    findings: list[Finding] = []
    for para, starts in seen.items():
        if len(starts) >= 2:
            findings.append(
                _finding(
                    "degeneration",
                    SEVERITY_HIGH,
                    _rel(workspace, path),
                    starts[0],
                    f"同一段落在本文件内逐字重复 {len(starts)} 次（行 {starts[:5]}）",
                    para,
                )
            )
    return findings


def check_degeneration(workspace: Path) -> list[Finding]:
    """D6：模板爆破、完全相同行重复、同文件逐字段落重复。"""
    findings: list[Finding] = []
    for _, path in _chapter_files(workspace):
        text = _read(path)
        lines = text.splitlines()
        findings.extend(_check_template_burst(workspace, path, lines))
        findings.extend(_check_identical_lines(workspace, path, lines))
        findings.extend(_check_dup_paragraphs(workspace, path, text))
    return findings


# ---------------------------------------------------------------------------
# 2. word_count（D2，GD-13）
# ---------------------------------------------------------------------------


def _parse_reported_counts(text: str) -> dict[tuple[str, int], int]:
    """从 progress.md 提取自报字数：{(scope, chapter_no): claimed}。

    scope: "chapter"（按章节号）/ "total"（章节文件合计）/ "report"（合并报告）。
    """
    claims: dict[tuple[str, int], int] = {}

    def put(scope: str, chapter: int, raw: str) -> None:
        claims.setdefault((scope, chapter), int(raw.replace(",", "")))

    for line in text.splitlines():
        if _is_table_line(line):
            cells = _split_row(line)
            if cells and re.fullmatch(r"\d{1,2}", cells[0]):
                for cell in cells[1:]:
                    if re.fullmatch(r"[\d,]{4,}", cell):
                        put("chapter", int(cells[0]), cell)
                        break
    for m in re.finditer(r"章节文件字数[^\d]{0,8}([\d,]{4,})", text):
        put("total", 0, m.group(1))
    for m in re.finditer(r"合并报告字数[^\d]{0,8}([\d,]{4,})", text):
        put("report", 0, m.group(1))
    for m in re.finditer(r"第\s*(\d{1,2})\s*章[^\n]{0,30}?[~约]\s*([\d,]{4,})", text):
        put("chapter", int(m.group(1)), m.group(2))
    return claims


def check_word_count(workspace: Path) -> list[Finding]:
    """D2/GD-13：实测非空白字符数，与 progress.md 自报字数比对，偏差 >10% 报 finding。"""
    progress_path = workspace / PROGRESS_FILENAME
    if not progress_path.is_file():
        return []
    claims = _parse_reported_counts(_read(progress_path))
    if not claims:
        return []

    chapters = _chapter_files(workspace)
    measured = {num: _non_ws(_read(p)) for num, p in chapters}
    total_chapters = sum(measured.values())
    report_path = workspace / REPORT_FILENAME
    total_report = _non_ws(_read(report_path)) if report_path.is_file() else None

    findings: list[Finding] = []
    file_map = {num: _rel(workspace, p) for num, p in chapters}
    for (scope, chapter), claimed in sorted(claims.items()):
        if scope == "chapter":
            if chapter not in measured:
                continue
            actual = measured[chapter]
            label = f"第{chapter}章"
            file = file_map[chapter]
        elif scope == "total":
            actual = total_chapters
            label = "章节文件合计"
            file = PROGRESS_FILENAME
        else:
            if total_report is None:
                continue
            actual = total_report
            label = "合并报告"
            file = PROGRESS_FILENAME
        if actual == 0:
            continue
        deviation = abs(claimed - actual) / actual
        if deviation > WORD_COUNT_TOLERANCE:
            findings.append(
                _finding(
                    "word_count",
                    SEVERITY_MEDIUM,
                    file,
                    1,
                    f"GD-13 字数自报失真：{label}自报 {claimed:,}，实测非空白字符 {actual:,}，"
                    f"偏差 {deviation:.0%}",
                    f"claimed={claimed}, measured={actual}",
                )
            )
    return findings


# ---------------------------------------------------------------------------
# 3. ledger_schema（D5）
# ---------------------------------------------------------------------------


def _header_covers(header: list[str]) -> bool:
    """表头覆盖全部必需列；「口径/年份」这类复合列名按主列名计（v3 提示词的写法）。

    R1 快速轮发现：模型逐字遵循提示词写「口径/年份」，严格等值匹配会误报 schema
    不合规并级联触发 freshness 全章告警。
    """
    cells = {h.split("/")[0].strip() for h in header}
    return set(LEDGER_FIELDS) <= cells


def check_ledger_schema(workspace: Path) -> list[Finding]:
    """D5：data_ledger.md 必须含覆盖 LEDGER_FIELDS 全部列的结构化表格。"""
    ledger_path = workspace / LEDGER_FILENAME
    if not ledger_path.is_file():
        return [
            _finding(
                "ledger_schema",
                SEVERITY_HIGH,
                LEDGER_FILENAME,
                0,
                "缺少 data_ledger.md 数据台账",
                "",
            )
        ]
    tables = _parse_ledger_tables(_read(ledger_path))
    for _, header, _ in tables:
        if _header_covers(header):
            return []
    missing_hint = (
        f"未找到包含全部必需列（{'|'.join(LEDGER_FIELDS)}）的表头"
        if tables
        else "台账中没有结构化表格（自由文本台账）"
    )
    return [
        _finding(
            "ledger_schema",
            SEVERITY_HIGH,
            LEDGER_FILENAME,
            1,
            f"台账不符合结构化 schema：{missing_hint}",
            "",
        )
    ]


# ---------------------------------------------------------------------------
# 4. ledger_freshness（D5，GD-15）
# ---------------------------------------------------------------------------


def _ledger_chapter_refs(text: str) -> set[int]:
    """台账中「首现章节」列（schema 合规时）或全文里引用到的章节号集合。"""
    refs: set[int] = set()
    parsed = False
    for _, header, rows in _parse_ledger_tables(text):
        if _header_covers(header):
            parsed = True
            idx = header.index("首现章节")
            for _, cells in rows:
                if idx < len(cells):
                    for m in re.finditer(r"(\d{1,2})", cells[idx]):
                        refs.add(int(m.group(1)))
    if not parsed:
        for m in re.finditer(r"第\s*(\d{1,2})\s*章", text):
            refs.add(int(m.group(1)))
    return refs


def check_ledger_freshness(workspace: Path) -> list[Finding]:
    """D5/GD-15：章节含精确数值但台账无对应「首现章节」条目 → 台账停更。"""
    ledger_path = workspace / LEDGER_FILENAME
    if not ledger_path.is_file():
        return []
    refs = _ledger_chapter_refs(_read(ledger_path))
    findings: list[Finding] = []
    for num, path in _chapter_files(workspace):
        if num == 0:
            continue
        if APPENDIX_MARK in path.name:
            # 附录是二手汇总（数据来源清单/待核实清单），结构性不可能有「首现」数据
            continue
        text = _read(path)
        lines = text.splitlines()
        # 触发线选取跳过两类非数据行：字数声明（「约 7,000 字」）与 [待核实] 行
        # （附录《待核实数据清单》是对前文的二手汇总，不是首现数据——R1 快速轮发现）
        precise_line = next(
            (
                i
                for i, line in enumerate(lines, start=1)
                if TODO_MARK not in line
                and any(
                    not _is_word_count_decl(line, m) for m in PRECISE_VALUE_RE.findall(line)
                )
            ),
            None,
        )
        if precise_line is not None and num not in refs:
            findings.append(
                _finding(
                    "ledger_freshness",
                    SEVERITY_MEDIUM,
                    _rel(workspace, path),
                    precise_line,
                    f"台账停更：第{num}章含精确数值，但台账无「首现章节」对应该章的条目（GD-15）",
                    lines[precise_line - 1].strip(),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# 5. unsourced_precise（D1/D5，GD-06 高危清单）
# ---------------------------------------------------------------------------


def _is_word_count_decl(line: str, match: str) -> bool:
    """「约 7,000 字」这类字数声明不是数据点（R1 快速轮发现的 FP 类）。"""
    idx = line.find(match)
    return idx >= 0 and line[idx + len(match) :].lstrip().startswith("字")


def check_unsourced_precise(workspace: Path) -> list[Finding]:
    """GD-06：精确数值所在行 ±2 行内无来源署名且无 [待核实] → 高危清单。

    代码块（ASCII 图）内的数值：窗口扩大到整个块边界 ±2 行——图表的来源
    标注在块外题注处，从块内单行起算窗口会系统性误判。
    """
    findings: list[Finding] = []
    for _, path in _chapter_files(workspace):
        lines = _read(path).splitlines()
        fences = _fence_ranges(lines)
        for i, line in enumerate(lines):
            if _is_separator_row(line):
                continue
            matches = [
                m for m in PRECISE_VALUE_RE.findall(line) if not _is_word_count_decl(line, m)
            ]
            if not matches:
                continue
            fence = next(((s, e) for s, e in fences if s < i < e), None)
            if fence is not None:
                lo = max(0, fence[0] - SOURCE_WINDOW_LINES)
                hi = min(len(lines), fence[1] + SOURCE_WINDOW_LINES + 1)
            else:
                lo = max(0, i - SOURCE_WINDOW_LINES)
                hi = min(len(lines), i + SOURCE_WINDOW_LINES + 1)
            window = "\n".join(lines[lo:hi])
            # 双样式永久并存（设计文档_R4 §2.2）：旧式来源署名与新式 [n] 引用都合法
            if (
                SOURCE_RE.search(window)
                or CITATION_REF_RE.search(window)
                or TODO_MARK in window
            ):
                continue
            findings.append(
                _finding(
                    "unsourced_precise",
                    SEVERITY_HIGH,
                    _rel(workspace, path),
                    i + 1,
                    f"精确数值 {', '.join(matches)} 附近无来源标注且无 {TODO_MARK} 标记（GD-06）",
                    line.strip(),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# 6. numeric_consistency（D4，GD-07）
# ---------------------------------------------------------------------------


def _ledger_col_index(header: list[str], field: str) -> int | None:
    """按主列名找列下标（「口径/年份」按「口径」计）。"""
    for i, h in enumerate(header):
        if h.split("/")[0].strip() == field:
            return i
    return None


def check_numeric_consistency(workspace: Path) -> list[Finding]:
    """GD-07：台账内同「指标名 + 口径/年份」出现不兼容「数值」→ 冲突。

    M1 修正（R3）：分组键补「口径/年份」——同指标多年份分行是台账的正常
    形态（round-3 曾因此误报 14 条）；同组内数值经 values_compatible 判定
    （粒度差异互为子串 = 兼容，如「2016 年」⊂「2016 年 6 月」）。倒挂检测暂不做。
    """
    ledger_path = workspace / LEDGER_FILENAME
    if not ledger_path.is_file():
        return []
    rows_by_name: dict[str, list[tuple[str, str, int]]] = {}
    for _, header, rows in _parse_ledger_tables(_read(ledger_path)):
        if not _header_covers(header):
            continue
        name_idx = _ledger_col_index(header, "指标名")
        value_idx = _ledger_col_index(header, "数值")
        caliber_idx = _ledger_col_index(header, "口径")
        if name_idx is None or value_idx is None:
            continue
        for lineno, cells in rows:
            if name_idx >= len(cells) or value_idx >= len(cells):
                continue
            caliber = (
                cells[caliber_idx].strip()
                if caliber_idx is not None and caliber_idx < len(cells)
                else ""
            )
            rows_by_name.setdefault(cells[name_idx], []).append(
                (caliber, cells[value_idx], lineno)
            )
    findings: list[Finding] = []
    for name, items in rows_by_name.items():
        # 口径兼容聚类：「2024年报」⊂「2024年报实算（…）」视为同口径（GD-09 型），
        # 「2024年报」vs「2025年报」不兼容（多年份分行，round-3 误报型）
        clusters: list[list[tuple[str, str, int]]] = []
        for item in items:
            for cluster in clusters:
                if values_compatible(item[0], cluster[0][0]):
                    cluster.append(item)
                    break
            else:
                clusters.append([item])
        for cluster in clusters:
            entries = [(value, lineno) for _caliber, value, lineno in cluster]
            # 两两兼容判定：同义重复（粒度差异）合并；存在互不兼容即冲突
            distinct: list[tuple[str, int]] = []
            for value, lineno in entries:
                if not any(values_compatible(value, kept) for kept, _ in distinct):
                    distinct.append((value, lineno))
            if len(distinct) > 1:
                first_line = entries[0][1]
                detail = "、".join(f"{v}（行 {ln}）" for v, ln in distinct)
                findings.append(
                    _finding(
                        "numeric_consistency",
                        SEVERITY_HIGH,
                        LEDGER_FILENAME,
                        first_line,
                        f"数值冲突：指标「{name}」在台账中有 {len(distinct)} 个不同数值（GD-07）",
                        detail,
                    )
                )
    return findings


# ---------------------------------------------------------------------------
# 7. figure_companion（D7）
# ---------------------------------------------------------------------------


def check_figure_companion(workspace: Path) -> list[Finding]:
    """D7：每个表格/图/代码块 ±3 行内需有来源署名，块后需紧跟解读段落。"""
    findings: list[Finding] = []
    for _, path in _chapter_files(workspace):
        lines = _read(path).splitlines()
        fences = _fence_ranges(lines)
        fence_at = {s: e for s, e in fences}
        blocks: list[tuple[int, int]] = []  # (start, end) 0-based inclusive
        i = 0
        while i < len(lines):
            if i in fence_at:
                # ASCII 图/文本示意图整块算一个图块
                blocks.append((i, fence_at[i]))
                i = fence_at[i] + 1
            elif _is_table_line(lines[i]):
                start = i
                while i < len(lines) and (_is_table_line(lines[i]) or _is_separator_row(lines[i])):
                    i += 1
                blocks.append((start, i - 1))
            elif re.match(r"\s*(!|\*?\*?图\s*\d)", lines[i]):
                blocks.append((i, i))
                i += 1
            else:
                i += 1
        for start, end in blocks:
            lo = max(0, start - FIGURE_WINDOW_LINES)
            hi = min(len(lines), end + FIGURE_WINDOW_LINES + 1)
            window = "\n".join(lines[lo:hi])
            has_source = (
                SOURCE_RE.search(window) is not None
                or CITATION_REF_RE.search(window) is not None
            )
            has_commentary = any(
                lines[j].strip()
                and not _is_table_line(lines[j])
                and not lines[j].strip().startswith("```")
                for j in range(end + 1, min(len(lines), end + FIGURE_WINDOW_LINES + 1))
            )
            missing = []
            if not has_source:
                missing.append("来源标注")
            if not has_commentary:
                missing.append("解读文字")
            if missing:
                findings.append(
                    _finding(
                        "figure_companion",
                        SEVERITY_LOW,
                        _rel(workspace, path),
                        start + 1,
                        f"表格/图题块缺少{'与'.join(missing)}（附近 ±{FIGURE_WINDOW_LINES} 行）",
                        lines[start].strip(),
                    )
                )
    return findings


# ---------------------------------------------------------------------------
# 8. structure（D8 + GD-16）
# ---------------------------------------------------------------------------

_DATE_RES = [
    re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})"),
    re.compile(r"(\d{4})年(\d{1,2})月(?:(\d{1,2})日?)?"),
]


def _find_dates(text: str) -> list[date]:
    out: list[date] = []
    for pattern in _DATE_RES:
        for m in pattern.finditer(text):
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3) or 1)
            try:
                out.append(date(y, mo, d))
            except ValueError:
                continue
    return out


def _check_dates(workspace: Path) -> list[Finding]:
    findings: list[Finding] = []
    today = date.today()
    for name in (PROGRESS_FILENAME, REPORT_FILENAME):
        path = workspace / name
        if not path.is_file():
            continue
        stale = sorted(
            {d for d in _find_dates(_read(path)) if abs((today - d).days) > DATE_SKEW_MAX_DAYS}
        )
        if stale:
            findings.append(
                _finding(
                    "structure",
                    SEVERITY_MEDIUM,
                    name,
                    1,
                    f"GD-16 文档日期与当前日期（{today.isoformat()}）偏差超过 "
                    f"{DATE_SKEW_MAX_DAYS} 天：{', '.join(d.isoformat() for d in stale[:5])}",
                    stale[0].isoformat(),
                )
            )
    return findings


def check_structure(workspace: Path) -> list[Finding]:
    """D8/GD-16：章节编号连续性、空壳章、日期 sanity、跨章逐字重复段落。"""
    findings: list[Finding] = []
    chapters = _chapter_files(workspace)
    if not chapters:
        findings.append(
            _finding(
                "structure",
                SEVERITY_HIGH,
                CHAPTERS_DIR,
                0,
                "缺少 chapters/ 目录或其中无章节文件",
                "",
            )
        )
        return findings + _check_dates(workspace)

    numbered = [
        num
        for num, path in chapters
        if num > 0 and _non_ws(_read(path)) >= SHELL_CHAPTER_MIN_CHARS
    ]
    if numbered:
        missing = sorted(set(range(min(numbered), max(numbered) + 1)) - set(numbered))
        if missing:
            findings.append(
                _finding(
                    "structure",
                    SEVERITY_MEDIUM,
                    CHAPTERS_DIR,
                    0,
                    f"章节编号不连续，缺失：{', '.join(str(n) for n in missing)}",
                    "",
                )
            )

    para_locations: dict[str, list[tuple[str, int]]] = {}
    for _, path in chapters:
        text = _read(path)
        if _non_ws(text) < SHELL_CHAPTER_MIN_CHARS:
            findings.append(
                _finding(
                    "structure",
                    SEVERITY_MEDIUM,
                    _rel(workspace, path),
                    1,
                    f"空壳章：正文非空白字符 {_non_ws(text)} < {SHELL_CHAPTER_MIN_CHARS}",
                    "",
                )
            )
        for start, para in _paragraphs(text):
            if len(para) >= DUP_PARAGRAPH_MIN_CHARS:
                para_locations.setdefault(para, []).append((path.name, start))

    for para, locs in para_locations.items():
        files = sorted({name for name, _ in locs})
        if len(files) >= 2:
            findings.append(
                _finding(
                    "structure",
                    SEVERITY_MEDIUM,
                    f"{CHAPTERS_DIR}/{files[0]}",
                    locs[0][1],
                    f"跨章逐字重复段落：出现在 {'、'.join(files)}（GD-12）",
                    para,
                )
            )
    return findings + _check_dates(workspace)


# ---------------------------------------------------------------------------
# 汇总入口
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 8. conclusion_source（M3：结论章出处锚点核对，内含区间比对）
# ---------------------------------------------------------------------------

_ANCHOR_LINE_RE = re.compile(r"[（(]\s*见第\s*(\d+)\s*章")
_CONCLUSION_NAME_RE = re.compile(r"结论")


def check_conclusion_source(workspace: Path) -> list[Finding]:
    """结论章「数字 +（见第 X 章）」锚点回查（GD-19/E1 型、GD-20/E9 型）。

    对结论章每个带出处锚点的行：抽取行内数值（锚点章号与年份除外），
    到被引用章节检索同值证据（spans_support：点值相对容差命中 / 区间
    真实重叠）。找不到即报。

    已知盲区（设计文档_R3 §3.2）：未挂锚点的正文间区间冲突不覆盖，深审兜底。
    """
    chapter_files = _chapter_files(workspace)
    conclusions = [(num, p) for num, p in chapter_files if _CONCLUSION_NAME_RE.search(p.name)]
    if not conclusions:
        return []
    _, conclusion_path = conclusions[0]

    chapter_spans: dict[int, list] = {}
    for num, path in chapter_files:
        if path == conclusion_path or num == 0:
            continue
        chapter_spans[num] = extract_spans(_read(path))

    findings: list[Finding] = []
    rel = _rel(workspace, conclusion_path)
    for lineno, line in enumerate(_read(conclusion_path).splitlines(), start=1):
        anchors = [int(a) for a in _ANCHOR_LINE_RE.findall(line)]
        if not anchors:
            continue
        for span in extract_spans(line):
            supported = False
            missing_anchor = False
            for anchor in anchors:
                spans = chapter_spans.get(anchor)
                if spans is None:
                    missing_anchor = True
                    continue
                if any(spans_support(span, cand) for cand in spans):
                    supported = True
                    break
            if supported:
                continue
            if missing_anchor and not any(a in chapter_spans for a in anchors):
                msg = (
                    f"结论章数值 {span.raw} 的出处锚点指向不存在的章节"
                    f"（第 {'、'.join(str(a) for a in anchors)} 章）（GD-19）"
                )
            else:
                msg = (
                    f"结论章数值 {span.raw} 标注（见第 {'、'.join(str(a) for a in anchors)} 章）"
                    f"但在该章未找到一致数值（GD-19/GD-20）"
                )
            findings.append(
                _finding(
                    "conclusion_source",
                    SEVERITY_HIGH,
                    rel,
                    lineno,
                    msg,
                    line.strip()[:120],
                )
            )
    return findings


# ---------------------------------------------------------------------------
# 10. 引用三级判定（设计文档_R4 §2.2：悬空/孤儿/未注册编号）
# ---------------------------------------------------------------------------


def check_citation_dangling(workspace: Path) -> list[Finding]:
    """正文 [n] 在章末参考资料节无对应条目（悬空引用）→ high。"""
    findings: list[Finding] = []
    for _, path in _chapter_files(workspace):
        text = _read(path)
        entries = parse_reference_section(text)
        body_refs = extract_citation_refs(strip_reference_section(text))
        for n in sorted(body_refs - set(entries)):
            findings.append(
                _finding(
                    "citation_dangling",
                    SEVERITY_HIGH,
                    _rel(workspace, path),
                    0,
                    f"悬空引用：正文 [{n}] 在章末参考资料节无对应条目",
                    f"[{n}]",
                )
            )
    return findings


def check_citation_orphan(workspace: Path) -> list[Finding]:
    """章末参考资料条目未被正文引用（孤儿条目，凑数假装饰）→ medium。"""
    findings: list[Finding] = []
    for _, path in _chapter_files(workspace):
        text = _read(path)
        entries = parse_reference_section(text)
        if not entries:
            continue
        body_refs = extract_citation_refs(strip_reference_section(text))
        for n in sorted(set(entries) - body_refs):
            findings.append(
                _finding(
                    "citation_orphan",
                    SEVERITY_MEDIUM,
                    _rel(workspace, path),
                    0,
                    f"孤儿条目：章末参考资料 [{n}] 未被正文引用",
                    entries[n],
                )
            )
    return findings


def check_citation_ledger_ref(workspace: Path) -> list[Finding]:
    """正文 [n] 须在台账 ref 列注册过 → high；台账无 ref 列（旧宿主）整体豁免。"""
    ledger_path = workspace / LEDGER_FILENAME
    if not ledger_path.is_file():
        return []
    registered = parse_ledger_refs(_read(ledger_path))
    if registered is None:
        return []
    findings: list[Finding] = []
    for _, path in _chapter_files(workspace):
        body_refs = extract_citation_refs(strip_reference_section(_read(path)))
        for n in sorted(body_refs - registered):
            findings.append(
                _finding(
                    "citation_ledger_ref",
                    SEVERITY_HIGH,
                    _rel(workspace, path),
                    0,
                    f"未注册编号：正文 [{n}] 未在 data_ledger.md 的 ref 列登记取号",
                    f"[{n}]",
                )
            )
    return findings


# ---------------------------------------------------------------------------
# 11. 占位符扫描（设计文档_R4 §5.2，GD-21）
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"XX[亿万百千个%]?|待补充|待填写|此处省略|▢")


def check_placeholder_scan(workspace: Path) -> list[Finding]:
    """GD-21：正文出现「XX 亿」「待补充」类占位符 → high。"""
    findings: list[Finding] = []
    for _, path in _chapter_files(workspace):
        for i, line in enumerate(_read(path).splitlines()):
            m = _PLACEHOLDER_RE.search(line)
            if m:
                findings.append(
                    _finding(
                        "placeholder_scan",
                        SEVERITY_HIGH,
                        _rel(workspace, path),
                        i + 1,
                        f"占位符泄漏：{m.group(0)!r}（GD-21）",
                        line.strip(),
                    )
                )
    return findings


# ---------------------------------------------------------------------------
# 12. 同章反复（设计文档_R4 §5.3，GD-27）
#
# 只查同章（用户裁定：跨章换措辞复用 = 正常笔法）。逐字段落/行重复已由
# degeneration 覆盖，本检查器专查非逐字的「同案例复述」：相同主体 +
# 相同精确数值组的段落对。语义级同义反复不做（R4 观察项）。
# ---------------------------------------------------------------------------

_REDUNDANCY_MIN_NUMBERS = 2
_REDUNDANCY_MIN_PARA_CHARS = 30
_ENTITY_RUN_RE = re.compile(r"[一-鿿]{4,}")
# 通用短语不算主体（防误报）
_ENTITY_STOPLIST = {
    "数据来源", "本章参考", "参考资料", "参考文献", "同比增长", "数据显示",
    "由此可见", "总体来看", "值得注意的是", "进一步分析",
}


def _para_numbers(line: str) -> frozenset[str]:
    return frozenset(PRECISE_VALUE_RE.findall(line))


def _entity_runs(line: str) -> list[str]:
    return [r for r in _ENTITY_RUN_RE.findall(line) if r not in _ENTITY_STOPLIST]


def _shares_entity(a: str, b: str, runs_a: list[str], runs_b: list[str]) -> bool:
    for ra in runs_a:
        if ra in b:
            return True
    for rb in runs_b:
        if rb in a:
            return True
    return False


def check_intra_chapter_redundancy(workspace: Path) -> list[Finding]:
    """GD-27：同章内同主体+同数值组的非逐字段落对（注水复述）→ medium。"""
    findings: list[Finding] = []
    for _, path in _chapter_files(workspace):
        paras: list[tuple[int, str, frozenset[str], list[str]]] = []
        for i, line in enumerate(_read(path).splitlines()):
            stripped = line.strip()
            if (
                len(stripped) < _REDUNDANCY_MIN_PARA_CHARS
                or stripped.startswith("#")
                or _is_table_line(stripped)
                or _is_separator_row(stripped)
            ):
                continue
            nums = _para_numbers(stripped)
            if len(nums) < _REDUNDANCY_MIN_NUMBERS:
                continue
            paras.append((i, stripped, nums, _entity_runs(stripped)))
        reported: set[tuple[int, int]] = set()
        for x in range(len(paras)):
            for y in range(x + 1, len(paras)):
                ia, ta, na, ra = paras[x]
                ib, tb, nb, rb = paras[y]
                if na != nb or (ia, ib) in reported:
                    continue
                if _shares_entity(ta, tb, ra, rb):
                    reported.add((ia, ib))
                    findings.append(
                        _finding(
                            "intra_chapter_redundancy",
                            SEVERITY_MEDIUM,
                            _rel(workspace, path),
                            ib + 1,
                            f"同章反复：行 {ia + 1} 与行 {ib + 1} 同主体同数值组复述（GD-27）",
                            tb[:80],
                        )
                    )
    return findings


CHECKERS: dict[str, Callable[[Path], list[Finding]]] = {
    "degeneration": check_degeneration,
    "word_count": check_word_count,
    "ledger_schema": check_ledger_schema,
    "ledger_freshness": check_ledger_freshness,
    "unsourced_precise": check_unsourced_precise,
    "numeric_consistency": check_numeric_consistency,
    "figure_companion": check_figure_companion,
    "structure": check_structure,
    "conclusion_source": check_conclusion_source,
    "citation_dangling": check_citation_dangling,
    "citation_orphan": check_citation_orphan,
    "citation_ledger_ref": check_citation_ledger_ref,
    "placeholder_scan": check_placeholder_scan,
    "intra_chapter_redundancy": check_intra_chapter_redundancy,
}


def run_all_checks(workspace: Path) -> list[Finding]:
    findings: list[Finding] = []
    for checker in CHECKERS.values():
        findings.extend(checker(workspace))
    return findings
