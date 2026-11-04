"""交付验收断言（设计文档_R4 §6.1）：三格式齐备 + 残留复查。

移植自同事管线 validate_report.py，改动：
- 函数化返回失败信息列表（空 = 全过），不再 RuntimeError 硬崩；
- 放宽「图表数=章节数」假设：只有存在 chart_manifest.json 时才做图表断言
  （我们不设每章一图配额）；
- 残留检查并入我们的 FORBIDDEN_PATTERNS（含中文模式）。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import zipfile
from html.parser import HTMLParser
from pathlib import Path

from octop_harness.pipeline.clean_report_markup import FORBIDDEN_PATTERNS
from octop_harness.report_qa.checks import PLACEHOLDER_RE

_MIN_BYTES = 10_000
_MD_RESIDUE = [r"\*\*", r"\|\s*:?-{2,}:?\s*\|", r"graph TD", r"```"]


class _AuditHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.skip = 0
        self.text: list[str] = []
        self.charts = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set(attrs.get("class", "").split())
        if tag in ("script", "style"):
            self.skip += 1
        if "chart" in classes:
            self.charts += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.text.append(data)


def _scan_text_residues(text: str, label: str) -> list[str]:
    """统一扫描某格式的提取文本：Markdown 残留 + 生成残留禁词 + 占位符（终审修复 2a）。"""
    failures: list[str] = []
    for pattern in _MD_RESIDUE:
        if re.search(pattern, text):
            failures.append(f"{label} 存在 Markdown 残留：{pattern}")
    for p in FORBIDDEN_PATTERNS:
        if p.search(text):
            failures.append(f"{label} 存在生成残留：{p.pattern}")
    m = PLACEHOLDER_RE.search(text)
    if m:
        failures.append(f"{label} 存在占位符：{m.group(0)}")
    return failures


def _pdf_text(pdf: Path) -> str | None:
    """pdftotext 提取 PDF 文本（poppler 软依赖；不可用返回 None = 跳过文本检查）。"""
    try:
        # 不用 text=True：Windows 中文locale 会按 GBK 解码 pdftotext 的 UTF-8 输出
        result = subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return result.stdout.decode("utf-8", errors="replace")


def validate_report(output_dir: Path, base_name: str = "report") -> list[str]:
    """返回验收失败信息列表；空列表 = 全过。"""
    output_dir = Path(output_dir)
    failures: list[str] = []

    # 三格式齐备 + 体量
    for fmt in ("docx", "html", "pdf"):
        f = output_dir / f"{base_name}.{fmt}"
        if not f.is_file():
            failures.append(f"缺 {fmt} 输出：{f.name}")
        elif f.stat().st_size < _MIN_BYTES:
            failures.append(f"{fmt} 输出异常小：{f.stat().st_size}B")

    # HTML 可见文本残留（同事禁词 + 我们的中英文禁词）
    html = output_dir / f"{base_name}.html"
    if html.is_file():
        parser = _AuditHTML()
        parser.feed(html.read_text(encoding="utf-8"))
        visible = "\n".join(parser.text)
        failures.extend(_scan_text_residues(visible, "HTML 可见文本"))

    # DOCX 媒体与残留
    docx = output_dir / f"{base_name}.docx"
    if docx.is_file():
        with zipfile.ZipFile(docx) as z:
            media = [n for n in z.namelist() if n.startswith("word/media/")]
            document = z.read("word/document.xml").decode("utf-8")
        manifest = output_dir / "chart_manifest.json"
        if manifest.is_file():
            expected = len(json.loads(manifest.read_text(encoding="utf-8")))
            if len(media) < expected:
                failures.append(f"Word 图表/公式图片不足：{len(media)} < {expected}")
        text = re.sub(r"<[^>]+>", "", document)
        failures.extend(_scan_text_residues(text, "Word"))

    # PDF 页数（pdfinfo 软依赖，缺失跳过）
    pdf = output_dir / f"{base_name}.pdf"
    if pdf.is_file():
        try:
            result = subprocess.run(
                ["pdfinfo", str(pdf)], capture_output=True, text=True, check=True
            )
            m = re.search(r"^Pages:\s+(\d+)", result.stdout, re.M)
            if m and int(m.group(1)) < 1:
                failures.append("PDF 页数为 0")
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass
        text = _pdf_text(pdf)
        if text is None:
            # fail-closed（终审复验补修）：工具缺失 = 未检查，未检查 ≠ 通过
            failures.append("PDF 文本未检查：pdftotext 不可用或提取失败")
        else:
            failures.extend(_scan_text_residues(text, "PDF"))

    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="交付验收断言")
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--base-name", default="report")
    args = parser.parse_args()
    failures = validate_report(args.output_dir, args.base_name)
    if failures:
        for f in failures:
            print(f"FAIL {f}")
        raise SystemExit(1)
    print("PASSED")


if __name__ == "__main__":
    main()
