"""报告生成残留清理（设计文档_R4 §5.1，移植自同事管线 clean_report_markup.py）。

与原版差异：①面向我们 `chapters/NN_*.md` 的逐文件清理（非其单文件拼装稿）；
②禁词清单扩充中文模式（校对痕迹/大纲标签/续写指令——对标实证漏网项）；
③代码围栏内容永不触碰；④默认只扫描报告，``--fix`` 才改写文件。
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

# 生成残留禁词模式（对标分析实证漏网项 + 同事管线原模式）
FORBIDDEN_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"原文描述有误"),
    re.compile(r"建议改为"),
    re.compile(r"\(Deep Analysis\)", re.I),
    re.compile(r"\(New Case\)", re.I),
    re.compile(r"Start from the cut-off", re.I),
    re.compile(r"Here's a thinking process", re.I),
]


def scan_text(text: str) -> list[tuple[int, str]]:
    """扫描文本，返回 [(行号 1-based, 命中行)] 清单（围栏内不扫）。"""
    hits: list[tuple[int, str]] = []
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if any(p.search(line) for p in FORBIDDEN_PATTERNS):
            hits.append((lineno, line.strip()))
    return hits


def clean_text(text: str) -> tuple[str, dict[str, int]]:
    """删除残留行（围栏内容不动），收敛连续空行；返回 (清理后文本, 统计)。"""
    lines = text.splitlines(keepends=True)
    hit_lines = {lineno for lineno, _ in scan_text(text)}
    kept = [ln for i, ln in enumerate(lines, start=1) if i not in hit_lines]
    cleaned = "".join(kept)
    cleaned = re.sub(r"\n{4,}", "\n\n\n", cleaned)
    return cleaned, {"removed": len(hit_lines)}


def main() -> None:
    parser = argparse.ArgumentParser(description="扫描/清理报告目录的生成残留")
    parser.add_argument("report_dir", type=Path)
    parser.add_argument("--fix", action="store_true", help="就地改写命中文件（默认只报告）")
    args = parser.parse_args()
    chapters = sorted(args.report_dir.glob("chapters/*.md"))
    if not chapters:
        chapters = sorted(args.report_dir.glob("*.md"))
    total = 0
    for path in chapters:
        text = path.read_text(encoding="utf-8")
        hits = scan_text(text)
        if not hits:
            continue
        total += len(hits)
        print(f"{path.name}: {len(hits)} 处残留")
        for lineno, line in hits:
            print(f"  L{lineno}: {line[:80]}")
        if args.fix:
            cleaned, _ = clean_text(text)
            path.write_text(cleaned, encoding="utf-8")
            print(f"  已清理改写：{path.name}")
    print(f"TOTAL {total}")


if __name__ == "__main__":
    main()
