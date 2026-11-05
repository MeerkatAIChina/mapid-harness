#!/usr/bin/env python3
"""
Meerkat 文档工具链 v2: Markdown → HTML / PDF / Word（R4 移植版，去 xlsx/pptx）
业界级排版 + 数据图表（真正的图表对象 / SVG 矢量，非静态图片）。

用法 (命令行):
  python3 doc_tools.py md2html <input.md> <output.html>
  python3 doc_tools.py md2docx <input.md> <output.docx>
  python3 doc_tools.py md2pdf  <input.md> <output.pdf>

用法 (函数调用, 供 LLM / bridge):
  from doc_tools import convert
  convert("html", markdown_text, "/path/out.html")   # 支持 html/docx/pdf

图表 DSL (在 Markdown 中内嵌 fenced code block, 语言标签为 chart):
  ```chart
  {"type": "bar", "title": "月度销量", "categories": ["1月","2月","3月"],
   "series": [{"name": "销量", "data": [120, 150, 180]}]}
  ```

支持的图表 type:
  bar / barh / line / area / pie / donut / radar / scatter / funnel / gauge
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

# ======================================================================
# 常量: 字体 / 配色
# ======================================================================

# 字体目录: fonts/ = 从 Noto ttc 提取的 SC 子字体 (CFF 轮廓, matplotlib 支持)
#           fonts_ttf/ = cu2qu 转换后的 TrueType 轮廓 (reportlab 支持, 由 convert_fonts.py 生成)
_FONT_DIR = os.environ.get("LONG_REPORT_FONT_DIR", "/home/chinux/jupyterlab/meerkatai/fonts")
_FONT_DIR_TTF = os.environ.get("LONG_REPORT_FONT_DIR_TTF", "/home/chinux/jupyterlab/meerkatai/fonts_ttf")


def _resolve_mpl_fonts() -> tuple[str | None, str | None, str]:
    """matplotlib 用字体: 返回 (regular, bold, family)。支持 CFF 轮廓。"""
    candidates = [
        (
            os.path.join(_FONT_DIR, "NotoSansCJKsc-Regular.ttf"),
            os.path.join(_FONT_DIR, "NotoSansCJKsc-Bold.ttf"),
            "Noto Sans CJK SC",
        ),
        ("/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/PingFang.ttc", "PingFang SC"),
        ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc", "Microsoft YaHei"),
    ]
    for reg, bold, fam in candidates:
        if reg and os.path.exists(reg):
            return reg, bold, fam
    return None, None, "Helvetica"


def _resolve_pdf_fonts() -> tuple[str | None, str | None, str | None, str | None]:
    """reportlab 用字体: 返回 (regular, bold, serif_regular, serif_bold)。必须 TrueType 轮廓。"""
    candidates = [
        # 1. cu2qu 转换后的 Noto TrueType (业界顶级, 由 convert_fonts.py 生成)
        (
            os.path.join(_FONT_DIR_TTF, "NotoSansCJKsc-Regular.ttf"),
            os.path.join(_FONT_DIR_TTF, "NotoSansCJKsc-Bold.ttf"),
            os.path.join(_FONT_DIR_TTF, "NotoSerifCJKsc-Regular.ttf"),
            os.path.join(_FONT_DIR_TTF, "NotoSerifCJKsc-Bold.ttf"),
        ),
        # 2. Windows 系统字体 (立即可用): 微软雅黑 + 宋体
        (
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/msyhbd.ttc",
            "C:/Windows/Fonts/simsun.ttc",
            "C:/Windows/Fonts/simsun.ttc",
        ),
        # 3. 系统 TrueType 中文字体 (立即可用): Droid 黑体 + 文鼎明体
        (
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
            "/usr/share/fonts/truetype/arphic/uming.ttc",
            "/usr/share/fonts/truetype/arphic/uming.ttc",
        ),
    ]
    for reg, bold, sreg, sbold in candidates:
        if reg and os.path.exists(reg):
            return reg, bold, sreg, sbold
    return None, None, None, None


# 专业数据可视化配色 (AntV G2 经典色板)
PALETTE = [
    "#5B8FF9",
    "#61DDAA",
    "#65789B",
    "#F6BD16",
    "#7262FD",
    "#78D3F8",
    "#9661BC",
    "#F6903D",
    "#008685",
    "#F08BB4",
]

# HTML 主题色
_HTML_CSS = """
:root {
  --primary: #2563eb;
  --primary-soft: #eff6ff;
  --heading: #1e293b;
  --text: #334155;
  --muted: #64748b;
  --border: #e2e8f0;
  --bg-soft: #f8fafc;
  --accent: #0ea5e9;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: "Noto Sans CJK SC", "PingFang SC", "Microsoft YaHei", "Segoe UI", system-ui, sans-serif;
  color: var(--text);
  line-height: 1.8;
  background: #fff;
  font-size: 15px;
  -webkit-font-smoothing: antialiased;
}
/* ===== 封面 ===== */
.cover {
  padding: 96px 48px 72px;
  background: linear-gradient(135deg, #1e3a8a 0%, #2563eb 55%, #0ea5e9 100%);
  color: #fff;
  text-align: center;
  page-break-after: always;
}
.cover h1 { font-size: 36px; margin: 0 0 16px; font-weight: 700; letter-spacing: 1px; }
.cover .subtitle { font-size: 16px; opacity: .9; margin: 0 0 40px; }
.cover .meta { display: inline-block; padding: 6px 18px; border: 1px solid rgba(255,255,255,.5); border-radius: 999px; font-size: 13px; opacity: .95; }
/* ===== 目录 ===== */
.toc {
  max-width: 860px; margin: 0 auto; padding: 40px 32px;
}
.toc h2 { font-size: 20px; color: var(--heading); border-bottom: 2px solid var(--primary); padding-bottom: 8px; }
.toc ul { list-style: none; padding: 0; }
.toc li { margin: 6px 0; }
.toc a { color: var(--text); text-decoration: none; display: block; padding: 8px 14px; border-left: 3px solid transparent; border-radius: 0 6px 6px 0; transition: all .15s; }
.toc a:hover { background: var(--primary-soft); border-left-color: var(--primary); color: var(--primary); }
.toc .lvl2 { padding-left: 34px; font-size: 14px; color: var(--muted); }
/* ===== 正文 ===== */
main { max-width: 860px; margin: 0 auto; padding: 16px 32px 80px; }
h1, h2, h3, h4, h5, h6 { color: var(--heading); line-height: 1.4; margin: 1.6em 0 .6em; }
h1 { font-size: 28px; border-bottom: 3px solid var(--primary); padding-bottom: 10px; }
h2 { font-size: 22px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
h3 { font-size: 18px; }
h4 { font-size: 16px; }
p { margin: .7em 0; }
strong { color: var(--heading); }
a { color: var(--primary); }
code {
  background: var(--bg-soft); border: 1px solid var(--border); border-radius: 4px;
  padding: 1px 6px; font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace; font-size: 13px; color: #be123c;
}
pre {
  background: #0f172a; color: #e2e8f0; padding: 18px 20px; border-radius: 8px;
  overflow-x: auto; font-size: 13px; line-height: 1.6;
}
pre code { background: none; border: none; color: inherit; padding: 0; }
blockquote {
  margin: 1em 0; padding: 10px 20px; border-left: 4px solid var(--accent);
  background: var(--bg-soft); color: var(--muted); border-radius: 0 6px 6px 0;
}
table { border-collapse: collapse; width: 100%; margin: 1.2em 0; font-size: 14px; }
th, td { border: 1px solid var(--border); padding: 9px 12px; text-align: left; }
th { background: var(--primary); color: #fff; font-weight: 600; }
tr:nth-child(even) td { background: var(--bg-soft); }
tr:hover td { background: var(--primary-soft); }
img { max-width: 100%; border-radius: 6px; }
hr { border: none; border-top: 1px solid var(--border); margin: 2em 0; }
/* ===== 图表 ===== */
.figure {
  margin: 1.6em 0; padding: 16px; border: 1px solid var(--border); border-radius: 8px;
  background: #fff; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,.05);
}
.figure svg { max-width: 100%; height: auto; }
/* ===== 打印 ===== */
@media print {
  .cover { page-break-after: always; }
  .toc { page-break-after: always; }
  body { font-size: 12pt; }
  main { max-width: 100%; }
  a { color: inherit; }
  .figure { box-shadow: none; page-break-inside: avoid; }
}
@page { margin: 20mm; }
"""


# ======================================================================
# Markdown 解析器 (块级 + 行内)
# ======================================================================

_SCIENCE_COMMANDS = {
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "δ",
    "Delta": "Δ",
    "epsilon": "ε",
    "varepsilon": "ε",
    "zeta": "ζ",
    "eta": "η",
    "theta": "θ",
    "lambda": "λ",
    "mu": "μ",
    "nu": "ν",
    "pi": "π",
    "rho": "ρ",
    "sigma": "σ",
    "tau": "τ",
    "phi": "φ",
    "omega": "ω",
    "partial": "∂",
    "nabla": "∇",
    "sum": "∑",
    "prod": "∏",
    "sqrt": "√",
    "infty": "∞",
    "times": "×",
    "cdot": "×",
    "div": "÷",
    "pm": "±",
    "mp": "∓",
    "leq": "≤",
    "geq": "≥",
    "neq": "≠",
    "approx": "≈",
    "sim": "~",
    "to": "→",
    "rightarrow": "→",
    "leftarrow": "←",
    "in": "∈",
    "cap": "∩",
    "cup": "∪",
    "circ": "°",
    "log": "log",
    "ln": "ln",
    "exp": "exp",
    "min": "min",
    "max": "max",
    "sin": "sin",
    "cos": "cos",
    "tan": "tan",
}
_SUB = str.maketrans("0123456789+-=()aeinorstx", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑᵢₙₒᵣₛₜₓ")
_SUP = str.maketrans("0123456789+-=()in", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁱⁿ")


def _replace_braced(expr: str, names: str, arity: int, formatter: Callable[..., str]) -> str:
    """替换带平衡花括号的 LaTeX 命令，支持参数内的嵌套下标。"""
    match = re.search(r"\\(?:" + names + r")\s*", expr)
    if not match:
        return expr
    args, cursor = [], match.end()
    for _ in range(arity):
        if cursor >= len(expr) or expr[cursor] != "{":
            return expr
        depth, start = 1, cursor + 1
        cursor += 1
        while cursor < len(expr) and depth:
            if expr[cursor] == "{":
                depth += 1
            elif expr[cursor] == "}":
                depth -= 1
            cursor += 1
        if depth:
            return expr
        args.append(expr[start : cursor - 1])
    return expr[: match.start()] + formatter(*args) + expr[cursor:]


def _latex_to_text(expr: str) -> str:
    """把常用 LaTeX 科学表达转为可读 Unicode，不丢弃变量或运算关系。"""
    expr = expr.replace("\x0c", "\\f").replace("\t", "\\t")
    expr = re.sub(r"\s+", " ", expr).strip()
    # 平衡花括号解析可处理 \frac{N_{interface}}{N_{component}} 等嵌套结构。
    for _ in range(24):
        updated = _replace_braced(expr, r"[dt]?frac", 2, lambda a, b: f"({a})/({b})")
        updated = _replace_braced(updated, r"text|mathrm|mathbf|mathit|operatorname", 1, lambda a: a)
        updated = _replace_braced(updated, r"sqrt", 1, lambda a: f"√({a})")
        updated = _replace_braced(updated, r"dot", 1, lambda a: a + "̇")
        if updated == expr:
            break
        expr = updated
    expr = re.sub(r"\\([A-Za-z]+)", lambda m: _SCIENCE_COMMANDS.get(m.group(1), m.group(1)), expr)
    expr = expr.replace("\\%", "%").replace("\\,", " ").replace("\\;", " ").replace("\\!", "")
    expr = expr.replace("\\ ", " ").replace("\\{", "{").replace("\\}", "}")

    def script(match: re.Match[str]) -> str:
        marker, content = match.group(1), match.group(2).strip()
        # 不使用字体覆盖不完整的 Unicode 上下标字母，避免 PDF 方框。
        return ("[" + content + "]") if marker == "_" else ("^(" + content + ")")

    expr = re.sub(r"([_^])\{([^{}]*)\}", script, expr)
    expr = re.sub(r"([_^])([A-Za-z0-9+-])", script, expr)
    return expr.replace("{", "").replace("}", "").strip()


def _normalize_literal_science(text: str) -> str:
    """规范化原稿直接写出的 Unicode 上下标与变量下标，避免字体缺字。"""
    text = text.replace("⋅", "×").replace("∼", "~")
    sup = dict(zip("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁱⁿ", "0123456789+-=()in", strict=True))
    sub = dict(zip("₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑᵢₙₒᵣₛₜₓ", "0123456789+-=()aeinorstx", strict=True))
    text = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁱⁿ]+", lambda m: "^(" + "".join(sup.get(c, c) for c in m.group(0)) + ")", text)
    text = re.sub(
        r"[₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑᵢₙₒᵣₛₜₓ]+", lambda m: "[" + "".join(sub.get(c, c) for c in m.group(0)) + "]", text
    )
    return re.sub(r"(?<![A-Za-z0-9_])([A-Za-zΑ-ω]+)_([A-Za-z][A-Za-z0-9]*)", r"\1[\2]", text)


def _inline_science(text: str) -> str:
    """渲染行内公式；$/单位 是成本表达，不作为公式分隔符。"""
    text = _normalize_literal_science(text)
    text = re.sub(r"\$/([A-Za-z])", r"美元/\1", text)
    return re.sub(r"(?<!\$)\$(?!\$)([^$]+?)\$(?!\$)", lambda m: _latex_to_text(m.group(1)), text, flags=re.S)


_MATH_CACHE: dict[str, dict[str, Any]] = {}
_MATH_TMP: Path | None = None
_MATH_DPI = 180


_MATH_BACKEND = os.environ.get("LONG_REPORT_MATH_BACKEND", "mathtext")


def _math_assets_mathtext(expr: str) -> dict[str, Any]:
    """LaTeX → matplotlib mathtext 排版（零外部依赖，跨平台）。

    注意：mathtext 对公式内中文支持差（缺字形警告），公式重的文档用
    ``LONG_REPORT_MATH_BACKEND=chrome`` 回退（需 mathml_renderer 与 Chrome）。
    返回结构与 chrome 后端一致：png/svg/width/height/dpi/key/expr。
    """
    import hashlib

    key = hashlib.sha256(expr.strip().encode("utf-8")).hexdigest()[:16]
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    fig = Figure()
    fig.text(0, 0, f"${expr}$", fontsize=14)
    FigureCanvasAgg(fig).draw()  # type: ignore[no-untyped-call]
    buf_png = io.BytesIO()
    fig.savefig(buf_png, format="png", dpi=192, transparent=True, bbox_inches="tight", pad_inches=0.02)
    buf_svg = io.BytesIO()
    fig.savefig(buf_svg, format="svg", transparent=True, bbox_inches="tight", pad_inches=0.02)
    from PIL import Image as _PILImage

    png = buf_png.getvalue()
    with _PILImage.open(io.BytesIO(png)) as im:
        width, height = im.size
    return {
        "png": png,
        "svg": buf_svg.getvalue().decode("utf-8"),
        "width": width,
        "height": height,
        "dpi": 192,
        "key": key,
        "expr": expr,
    }


def _math_assets(expr: str) -> dict[str, Any]:
    """LaTeX → 数学排版，缓存尺寸供三种格式使用（后端见 _MATH_BACKEND）。"""
    import hashlib

    key = hashlib.sha256(expr.strip().encode("utf-8")).hexdigest()
    if key not in _MATH_CACHE:
        if _MATH_BACKEND == "chrome":
            from mathml_renderer import math_assets

            _MATH_CACHE[key] = math_assets(expr)
        else:
            _MATH_CACHE[key] = _math_assets_mathtext(expr)
    return _MATH_CACHE[key]


def _math_file(expr: str) -> str:
    """ReportLab 行内图片需要文件路径，按公式哈希缓存。"""
    global _MATH_TMP
    asset = _math_assets(expr)
    if _MATH_TMP is None:
        _MATH_TMP = Path(tempfile.mkdtemp(prefix="meerkat_math_"))
    path = _MATH_TMP / (asset["key"] + ".png")
    if not path.exists():
        path.write_bytes(asset["png"])
    return str(path)


def _math_placeholders(text: str, fmt: str) -> tuple[str, dict[str, str]]:
    """先替换成数学图片占位符，待 Markdown 行内样式处理完毕后恢复。"""
    assets: list[dict[str, Any]] = []
    text = _normalize_literal_science(text)
    text = re.sub(r"\$/([A-Za-z])", r"美元/\1", text)

    def replace(match: re.Match[str]) -> str:
        index = len(assets)
        asset = _math_assets(match.group(1))
        assets.append(asset)
        return f"@@MATH{index}@@"

    text = re.sub(r"(?<!\$)\$(?!\$)([^$]+?)\$(?!\$)", replace, text, flags=re.S)
    rendered = {}
    for index, asset in enumerate(assets):
        width = asset["width"] * 72 / asset["dpi"]
        height = asset["height"] * 72 / asset["dpi"]
        if fmt == "html":
            rendered[f"@@MATH{index}@@"] = (
                f'<span class="inline-math" role="math" '
                f'aria-label="{_escape_html(asset["expr"])}">{asset["svg"]}</span>'
            )
        else:
            rendered[f"@@MATH{index}@@"] = (
                f'<img src="{_math_file(asset["expr"])}" width="{width:.2f}" height="{height:.2f}" valign="-2"/>'
            )
    return text, rendered


def _strip_inline(text: str) -> str:
    """去除行内标记, 得到纯文本。"""
    text = _inline_science(text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return text


def _inline_to_html(text: str) -> str:
    """行内标记 → HTML (转义 + 加粗/斜体/代码/链接/数学图片)。"""
    text, math_images = _math_placeholders(text, "html")

    def esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    text = esc(text)
    # 先处理图片和链接
    text = re.sub(r"!\[([^\]]*)\]\(([^)]*)\)", r'<img src="\2" alt="\1">', text)
    text = re.sub(r"\[([^\]]*)\]\(([^)]*)\)", r'<a href="\2">\1</a>', text)
    # 加粗 / 斜体 / 行内代码
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    for token, image in math_images.items():
        text = text.replace(token, image)
    return text


def _parse_blocks(md_text: str) -> list[tuple[str, Any]]:
    """把 markdown 解析成结构化块: (type, payload)。"""
    blocks: list[tuple[str, Any]] = []
    lines = md_text.split("\n")
    i, n = 0, len(lines)

    def _chart_or_code(line: str, i: int) -> tuple[tuple[str, Any], int] | None:
        # fenced code block: ```chart ... ``` 或 ```lang ... ```
        m = re.match(r"^```\s*(\w*)\s*$", line.strip())
        if not m:
            return None
        lang = m.group(1)
        buf = []
        i += 1
        while i < n and not lines[i].strip().startswith("```"):
            buf.append(lines[i])
            i += 1
        # 原稿存在未闭合的普通代码围栏；不要把后续图表的開围栏误当作结尾。
        if i < n and lang != "chart" and lines[i].strip() == "```chart":
            return ("code", "\n".join(buf)), i
        if i < n:
            i += 1  # 跳过结尾 ```
        content = "\n".join(buf)
        if lang == "chart":
            try:
                return ("chart", json.loads(content)), i
            except Exception:
                return ("code", content), i
        return ("code", content), i

    while i < n:
        line = lines[i].rstrip()
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # 独立公式：$$...$$，支持单行、多行及公式后的同行为注。
        if stripped.startswith("$$"):
            rest = stripped[2:]
            if "$$" in rest:
                content, trailing = rest.split("$$", 1)
                i += 1
                blocks.append(("math", content.strip()))
                if trailing.strip():
                    blocks.append(("para", trailing.strip()))
                continue
            i += 1
            buf = []
            while i < n and "$$" not in lines[i]:
                buf.append(lines[i])
                i += 1
            trailing = ""
            if i < n:
                last, trailing = lines[i].split("$$", 1)
                buf.append(last)
                i += 1
            blocks.append(("math", " ".join(buf).strip()))
            if trailing.strip():
                blocks.append(("para", trailing.strip()))
            continue

        # fenced code / chart
        if stripped.startswith("```"):
            res = _chart_or_code(line, i)
            if res:
                blocks.append(res[0])
                i = res[1]
                continue

        # 标题
        m = re.match(r"^(#{1,6})\s+(.*)", stripped)
        if m:
            blocks.append(("heading", (len(m.group(1)), m.group(2).strip())))
            i += 1
            continue

        # 图片 (Markdown 内联 base64 图)
        m = re.match(r"!\[([^\]]*)\]\(data:image/([a-zA-Z]+);base64,([A-Za-z0-9+/=]+)\)", stripped)
        if m:
            blocks.append(("image", (m.group(1), m.group(2), m.group(3))))
            i += 1
            continue

        # 表格
        if stripped.startswith("|"):
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append(lines[i].strip())
                i += 1
            blocks.append(("table", rows))
            continue

        # 列表 (含嵌套, 只支持一级缩进)
        if re.match(r"^\s*([-*+]|\d+\.)\s+", stripped):
            items = []
            while i < n and re.match(r"^\s*([-*+]|\d+\.)\s+", lines[i].strip()):
                items.append(re.sub(r"^\s*([-*+]|\d+\.)\s+", "", lines[i].strip()))
                i += 1
            blocks.append(("list", items))
            continue

        # 引用
        if stripped.startswith(">"):
            quotes = []
            while i < n and lines[i].strip().startswith(">"):
                quotes.append(lines[i].strip().lstrip(">").strip())
                i += 1
            blocks.append(("quote", " ".join(quotes)))
            continue

        # 分隔线
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
            blocks.append(("hr", None))
            i += 1
            continue

        # 段落
        para = []
        while (
            i < n
            and lines[i].strip()
            and not re.match(r"^(#{1,6}\s|\||\s*[-*+]\s|\s*\d+\.\s|!\[|>|```|\$\$|-{3,}|\*{3,}|_{3,})", lines[i])
        ):
            para.append(lines[i].strip())
            i += 1
        blocks.append(("para", " ".join(para)))

    return blocks


def _parse_table_rows(rows: list[str]) -> tuple[list[str] | None, list[list[str]]]:
    """解析 markdown 表格行为 header + data 行 (跳过 |---| 分隔行)。"""
    header, data = None, []
    for r in rows:
        cells = [c.strip() for c in r.strip().strip("|").split("|")]
        if all(re.match(r"^:?-{2,}:?$", c) for c in cells):
            continue
        if header is None:
            header = cells
        elif cells not in data:
            data.append(cells)
    return header, data


def _extract_title(blocks: list[tuple[str, Any]]) -> str:
    """取第一个 H1 作为文档标题。"""
    for typ, payload in blocks:
        if typ == "heading" and payload[0] == 1:
            return _strip_inline(payload[1])
    return "文档"


# ======================================================================
# 图表引擎 (matplotlib)
# ======================================================================

_mpl_ready = False


def _mpl_setup() -> bool:
    """惰性初始化 matplotlib (字体 + 样式), 只做一次。"""
    global _mpl_ready
    if _mpl_ready:
        return True
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager

        reg, bold, fam = _resolve_mpl_fonts()
        registered = set()
        for p in (reg, bold):
            if p and os.path.exists(p) and p not in registered:
                try:
                    font_manager.fontManager.addfont(p)
                    registered.add(p)
                except Exception:
                    pass

        plt.rcParams.update(
            {
                "font.sans-serif": [fam, "DejaVu Sans"],
                "font.family": "sans-serif",
                "axes.unicode_minus": False,
                "figure.dpi": 110,
                "svg.fonttype": "path",
                "axes.edgecolor": "#cbd5e1",
                "axes.linewidth": 0.8,
                "axes.grid": True,
                "grid.color": "#e2e8f0",
                "grid.linewidth": 0.6,
                "axes.axisbelow": True,
                "axes.spines.top": False,
                "axes.spines.right": False,
                "axes.titlesize": 14,
                "axes.titleweight": "bold",
                "axes.labelsize": 11,
                "xtick.labelsize": 10,
                "ytick.labelsize": 10,
                "legend.frameon": False,
                "legend.fontsize": 10,
            }
        )
        _mpl_ready = True
        return True
    except Exception:
        return False


def _chart_axes(chart: dict[str, Any], fig: Any) -> Any:
    """根据图表高度返回 axes。"""
    h = float(chart.get("height") or 0)
    if h <= 0:
        # 默认高度
        n = len(chart.get("categories") or []) or 1
        h = max(3.2, min(8.0, 3.0 + n * 0.35))
    fig.set_size_inches(7.2, h)
    return fig.subplots()


def _bar_chart(ax: Any, chart: dict[str, Any]) -> None:
    import numpy as np

    cats = chart.get("categories") or []
    series = chart.get("series") or []
    x = np.arange(len(cats))
    width = 0.7 / max(len(series), 1)
    unit = chart.get("unit") or ""
    for si, s in enumerate(series):
        data = [float(v) for v in s.get("data", [])]
        off = (si - (len(series) - 1) / 2) * width
        bars = ax.bar(x + off, data, width=width, label=s.get("name", ""), color=PALETTE[si % len(PALETTE)], zorder=3)
        for b, v in zip(bars, data, strict=False):
            ax.text(b.get_x() + b.get_width() / 2, b.get_height(), f"{v:g}{unit}", ha="center", va="bottom", fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels(cats)
    if chart.get("x_label"):
        ax.set_xlabel(chart["x_label"])
    if chart.get("y_label"):
        ax.set_ylabel(chart["y_label"])
    if series and series[0].get("name"):
        ax.legend()


def _barh_chart(ax: Any, chart: dict[str, Any]) -> None:
    import numpy as np

    cats = chart.get("categories") or []
    series = chart.get("series") or []
    y = np.arange(len(cats))
    height = 0.7 / max(len(series), 1)
    unit = chart.get("unit") or ""
    for si, s in enumerate(series):
        data = [float(v) for v in s.get("data", [])]
        off = (si - (len(series) - 1) / 2) * height
        bars = ax.barh(
            y + off, data, height=height, label=s.get("name", ""), color=PALETTE[si % len(PALETTE)], zorder=3
        )
        for b, v in zip(bars, data, strict=False):
            ax.text(b.get_width(), b.get_y() + b.get_height() / 2, f" {v:g}{unit}", va="center", ha="left", fontsize=8)
    ax.set_yticks(y)
    ax.set_yticklabels(cats)
    if chart.get("x_label"):
        ax.set_xlabel(chart["x_label"])
    if series and series[0].get("name"):
        ax.legend()


def _line_chart(ax: Any, chart: dict[str, Any]) -> None:
    cats = chart.get("categories") or []
    series = chart.get("series") or []
    x = list(range(len(cats)))
    unit = chart.get("unit") or ""
    for si, s in enumerate(series):
        data = [float(v) for v in s.get("data", [])]
        ax.plot(
            x,
            data,
            marker="o",
            linewidth=2,
            markersize=5,
            label=s.get("name", ""),
            color=PALETTE[si % len(PALETTE)],
            zorder=3,
        )
        for xi, v in zip(x, data, strict=False):
            ax.text(xi, v, f"{v:g}{unit}", ha="center", va="bottom", fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels(cats)
    if chart.get("x_label"):
        ax.set_xlabel(chart["x_label"])
    if chart.get("y_label"):
        ax.set_ylabel(chart["y_label"])
    if series and series[0].get("name"):
        ax.legend()


def _area_chart(ax: Any, chart: dict[str, Any]) -> None:
    cats = chart.get("categories") or []
    series = chart.get("series") or []
    x = list(range(len(cats)))
    for si, s in enumerate(series):
        data = [float(v) for v in s.get("data", [])]
        ax.fill_between(x, data, alpha=0.25, color=PALETTE[si % len(PALETTE)])
        ax.plot(x, data, linewidth=2, label=s.get("name", ""), color=PALETTE[si % len(PALETTE)], zorder=3)
    ax.set_xticks(x)
    ax.set_xticklabels(cats)
    if chart.get("x_label"):
        ax.set_xlabel(chart["x_label"])
    if chart.get("y_label"):
        ax.set_ylabel(chart["y_label"])
    if series and series[0].get("name"):
        ax.legend()


def _pie_chart(ax: Any, chart: dict[str, Any], donut: bool = False) -> None:
    cats = chart.get("categories") or []
    series = chart.get("series") or []
    values = [float(v) for v in (series[0].get("data", []) if series else [])]
    if not values:
        return
    colors = [PALETTE[i % len(PALETTE)] for i in range(len(values))]
    if donut:
        wedges, _texts, _autotexts = ax.pie(
            values,
            labels=cats,
            colors=colors,
            startangle=90,
            wedgeprops=dict(width=0.42, edgecolor="white", linewidth=2),
            autopct=lambda p: f"{p:.1f}%",
        )
    else:
        wedges, _texts, _autotexts = ax.pie(
            values, labels=cats, colors=colors, startangle=90, autopct=lambda p: f"{p:.1f}%"
        )
    for w in wedges:
        w.set_edgecolor("white")
        w.set_linewidth(1.5)
    ax.axis("equal")


def _radar_chart(ax: Any, chart: dict[str, Any]) -> None:
    import numpy as np

    cats = chart.get("categories") or []
    series = chart.get("series") or []
    n = len(cats)
    if n < 3:
        return
    angles = np.linspace(0, 2 * np.pi, n, endpoint=False).tolist()
    angles += angles[:1]
    ax.set_theta_offset(np.pi / 2)
    ax.set_theta_direction(-1)
    for si, s in enumerate(series):
        data = [float(v) for v in s.get("data", [])]
        if len(data) != n:
            continue
        vals = data + data[:1]
        ax.plot(angles, vals, linewidth=2, label=s.get("name", ""), color=PALETTE[si % len(PALETTE)])
        ax.fill(angles, vals, alpha=0.2, color=PALETTE[si % len(PALETTE)])
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(cats, fontsize=10)
    if series and series[0].get("name"):
        ax.legend(loc="upper right", bbox_to_anchor=(1.25, 1.1))


def _scatter_chart(ax: Any, chart: dict[str, Any]) -> None:
    series = chart.get("series") or []
    for si, s in enumerate(series):
        pts = s.get("data", [])
        xs = [float(p[0]) for p in pts if isinstance(p, (list, tuple)) and len(p) >= 2]
        ys = [float(p[1]) for p in pts if isinstance(p, (list, tuple)) and len(p) >= 2]
        if xs:
            sizes = [35 + 25 * float(p[2]) if len(p) > 2 else 55 for p in pts]
            ax.scatter(xs, ys, s=sizes, alpha=0.8, label=s.get("name", ""), color=PALETTE[si % len(PALETTE)], zorder=3)
            for p in pts:
                if len(p) > 3:
                    ax.annotate(
                        str(p[3]), (float(p[0]), float(p[1])), xytext=(5, 5), textcoords="offset points", fontsize=8
                    )
    if chart.get("x_label"):
        ax.set_xlabel(chart["x_label"])
    if chart.get("y_label"):
        ax.set_ylabel(chart["y_label"])
    if series and series[0].get("name"):
        ax.legend()


def _funnel_chart(ax: Any, chart: dict[str, Any]) -> None:
    import numpy as np

    cats = chart.get("categories") or []
    series = chart.get("series") or []
    values = [float(v) for v in (series[0].get("data", []) if series else [])]
    if not values:
        return
    y = np.arange(len(values))
    # 按值大小绘制宽度递减的横向条, 居中
    for yi, v in enumerate(values):
        ax.barh(yi, v, height=0.6, color=PALETTE[yi % len(PALETTE)], zorder=3)
        ax.text(v, yi, f" {v:g}", va="center", ha="left", fontsize=8)
    ax.set_yticks(y)
    ax.set_yticklabels(cats)
    ax.invert_yaxis()
    ax.set_xlim(0, max(values) * 1.25)


def _gauge_chart(ax: Any, chart: dict[str, Any]) -> None:
    import numpy as np

    series = chart.get("series") or []
    value = float(series[0].get("data", [0])[0]) if series and series[0].get("data") else 0
    vmin = float(chart.get("min", 0))
    vmax = float(chart.get("max", 100))
    theta = np.linspace(np.pi, 0, 100)
    r = 1.0
    ax.plot(np.cos(theta) * r, np.sin(theta) * r, color="#cbd5e1", linewidth=12, solid_capstyle="round")
    ratio = max(0.0, min(1.0, (value - vmin) / (vmax - vmin) if vmax > vmin else 0))
    n_on = int(ratio * 100)
    ax.plot(
        np.cos(theta[: max(n_on, 1)]) * r,
        np.sin(theta[: max(n_on, 1)]) * r,
        color="#2563eb",
        linewidth=12,
        solid_capstyle="round",
    )
    ax.text(0, -0.25, f"{value:g}", ha="center", va="center", fontsize=28, fontweight="bold")
    ax.text(0, -0.55, chart.get("title") or "", ha="center", va="center", fontsize=10, color="#64748b")
    ax.set_xlim(-1.3, 1.3)
    ax.set_ylim(-0.8, 1.2)
    ax.axis("off")


def _flow_chart(ax: Any, chart: dict[str, Any]) -> None:
    import textwrap

    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

    stages = chart.get("categories") or []
    if not stages:
        return
    for i, stage in enumerate(stages):
        box = FancyBboxPatch(
            (i - 0.36, -0.16),
            0.72,
            0.32,
            boxstyle="round,pad=0.035,rounding_size=0.06",
            facecolor="#EEF3F6",
            edgecolor="#2F5D7C",
            linewidth=1.5,
        )
        ax.add_patch(box)
        ax.text(i, 0, textwrap.fill(stage, 7), ha="center", va="center", fontsize=9, color="#24313A")
        if i < len(stages) - 1:
            ax.add_patch(
                FancyArrowPatch(
                    (i + 0.38, 0), (i + 0.62, 0), arrowstyle="-|>", mutation_scale=13, color="#4E8A75", linewidth=1.5
                )
            )
    ax.set_xlim(-0.55, len(stages) - 0.45)
    ax.set_ylim(-0.55, 0.55)
    ax.axis("off")


def _graph_chart(ax: Any, chart: dict[str, Any]) -> None:
    import textwrap

    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

    nodes = {n["name"]: n for n in chart.get("nodes", [])}
    for link in chart.get("links", []):
        source, target = nodes.get(link.get("source")), nodes.get(link.get("target"))
        if not source or not target:
            continue
        arrow = FancyArrowPatch(
            (source["x"], source["y"]),
            (target["x"], target["y"]),
            arrowstyle="-|>",
            mutation_scale=12,
            color="#78909C",
            linewidth=1.2,
            connectionstyle="arc3,rad=0.06",
            shrinkA=28,
            shrinkB=28,
        )
        ax.add_patch(arrow)
        ax.text(
            (source["x"] + target["x"]) / 2,
            (source["y"] + target["y"]) / 2 + 0.035,
            link.get("label", ""),
            fontsize=7,
            color="#667780",
            ha="center",
        )
    for i, node in enumerate(nodes.values()):
        box = FancyBboxPatch(
            (node["x"] - 0.105, node["y"] - 0.055),
            0.21,
            0.11,
            boxstyle="round,pad=0.018,rounding_size=0.025",
            facecolor=PALETTE[i % len(PALETTE)],
            edgecolor="white",
            linewidth=1.2,
        )
        ax.add_patch(box)
        ax.text(
            node["x"],
            node["y"],
            textwrap.fill(node["name"], 9),
            color="white",
            fontsize=8,
            ha="center",
            va="center",
            fontweight="bold",
        )
    ax.set_xlim(-0.03, 1.03)
    ax.set_ylim(-0.03, 1.03)
    ax.set_aspect("equal")
    ax.axis("off")


def _heatmap_chart(ax: Any, chart: dict[str, Any]) -> None:
    import numpy as np
    from matplotlib.colors import LinearSegmentedColormap

    cats = chart.get("categories") or []
    series = chart.get("series") or []
    values = np.array([[float(v) for v in s.get("data", [])] for s in series])
    cmap = LinearSegmentedColormap.from_list("report", ["#EEF3F6", "#73A6B8", "#2F5D7C"])
    image = ax.imshow(values, cmap=cmap, aspect="auto", vmin=float(values.min()), vmax=float(values.max()))
    ax.set_xticks(range(len(cats)), cats)
    ax.set_yticks(range(len(series)), [s.get("name", "") for s in series])
    for y in range(values.shape[0]):
        for x in range(values.shape[1]):
            ax.text(
                x,
                y,
                f"{values[y, x]:g}",
                ha="center",
                va="center",
                fontsize=9,
                color="white" if values[y, x] > (values.min() + values.max()) / 2 else "#24313A",
            )
    ax.figure.colorbar(image, ax=ax, fraction=0.035, pad=0.03)


_CHART_RENDERERS: dict[str, Callable[[Any, dict[str, Any]], None]] = {
    "bar": _bar_chart,
    "barh": _barh_chart,
    "line": _line_chart,
    "area": _area_chart,
    "pie": _pie_chart,
    "donut": lambda ax, c: _pie_chart(ax, c, donut=True),
    "radar": _radar_chart,
    "scatter": _scatter_chart,
    "funnel": _funnel_chart,
    "gauge": _gauge_chart,
    "flow": _flow_chart,
    "graph": _graph_chart,
    "heatmap": _heatmap_chart,
}


def _chart_to_svg(chart: dict[str, Any]) -> str | None:
    """渲染图表为 SVG 字符串 (矢量, 供 HTML)。失败返回 None。"""
    if not _mpl_setup():
        return None
    try:
        import matplotlib.pyplot as plt

        fig = plt.figure()
        ax = _chart_axes(chart, fig)
        renderer = _CHART_RENDERERS.get(chart.get("type", "bar"))
        if not renderer:
            return None
        if chart.get("type") in ("radar", "pie", "donut", "gauge"):
            if chart.get("type") == "radar":
                ax.remove()
                ax = fig.add_subplot(111, polar=True)
            else:
                pass  # pie/donut/gauge 用默认矩形 ax 但 axis equal/off
        renderer(ax, chart)
        if chart.get("title"):
            ax.set_title(chart["title"])
        fig.tight_layout()
        buf = io.StringIO()
        fig.savefig(buf, format="svg")
        plt.close(fig)
        svg = buf.getvalue()
        m = re.search(r"<svg[\s\S]*</svg>", svg)
        return m.group(0) if m else None
    except Exception:
        return None


def _chart_to_png(chart: dict[str, Any], dpi: int = 220) -> bytes | None:
    """渲染图表为 PNG bytes (供 PDF/DOCX/XLSX/PPTX)。失败返回 None。"""
    if not _mpl_setup():
        return None
    try:
        import matplotlib.pyplot as plt

        fig = plt.figure()
        ax = _chart_axes(chart, fig)
        renderer = _CHART_RENDERERS.get(chart.get("type", "bar"))
        if not renderer:
            return None
        if chart.get("type") == "radar":
            ax.remove()
            ax = fig.add_subplot(111, polar=True)
        renderer(ax, chart)
        if chart.get("title"):
            ax.set_title(chart["title"])
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=dpi)
        plt.close(fig)
        return buf.getvalue()
    except Exception:
        return None


# ======================================================================
# HTML 渲染器 (自包含, 专业排版, 可打印 PDF)
# ======================================================================


def md_to_html(md_text: str, output_path: str | Path) -> str | Path:
    blocks = _parse_blocks(md_text)
    title = _extract_title(blocks)

    # 收集标题用于目录
    toc_entries = []
    for typ, payload in blocks:
        if typ == "heading" and payload[0] <= 2:
            toc_entries.append((payload[0], _strip_inline(payload[1])))
    has_toc = len(toc_entries) >= 3

    parts = []
    parts.append('<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="utf-8">')
    parts.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    parts.append(f"<title>{title}</title>")
    parts.append(f"<style>{_HTML_CSS}</style>\n</head>\n<body>")

    # 封面
    parts.append('<header class="cover">')
    parts.append(f"<h1>{title}</h1>")
    parts.append('<p class="subtitle">Meerkat AI · 智能报告</p>')
    parts.append('<span class="meta">由 AI 自动生成 · 数据图表</span>')
    parts.append("</header>")

    # 目录
    if has_toc:
        parts.append('<nav class="toc"><h2>目录</h2><ul>')
        for idx, (lvl, txt) in enumerate(toc_entries):
            cls = "lvl2" if lvl == 2 else ""
            parts.append(f'<li><a class="{cls}" href="#sec-{idx}">{txt}</a></li>')
        parts.append("</ul></nav>")

    # 正文
    parts.append("<main>")
    h_idx = 0
    for typ, payload in blocks:
        if typ == "heading":
            lvl, txt = payload
            if lvl <= 2:
                anchor = f"sec-{h_idx}"
                h_idx += 1
                parts.append(f'<h{min(lvl, 6)} id="{anchor}">{_inline_to_html(txt)}</h{min(lvl, 6)}>')
            else:
                parts.append(f"<h{min(lvl, 6)}>{_inline_to_html(txt)}</h{min(lvl, 6)}>")
        elif typ == "para":
            parts.append(f"<p>{_inline_to_html(payload)}</p>")
        elif typ == "list":
            parts.append("<ul>")
            for item in payload:
                parts.append(f"<li>{_inline_to_html(item)}</li>")
            parts.append("</ul>")
        elif typ == "quote":
            parts.append(f"<blockquote>{_inline_to_html(payload)}</blockquote>")
        elif typ == "hr":
            parts.append("<hr>")
        elif typ == "code":
            parts.append(f"<pre><code>{_escape_html(payload)}</code></pre>")
        elif typ == "image":
            alt, fmt, b64 = payload
            parts.append(f'<img src="data:image/{fmt};base64,{b64}" alt="{alt}">')
        elif typ == "table":
            header, data = _parse_table_rows(payload)
            parts.append("<table>")
            if header:
                parts.append(
                    "<thead><tr>" + "".join(f"<th>{_inline_to_html(c)}</th>" for c in header) + "</tr></thead>"
                )
            parts.append("<tbody>")
            for row in data:
                parts.append("<tr>" + "".join(f"<td>{_inline_to_html(c)}</td>" for c in row) + "</tr>")
            parts.append("</tbody></table>")
        elif typ == "chart":
            svg = _chart_to_svg(payload)
            if svg:
                parts.append(f'<div class="figure">{svg}</div>')
            else:
                parts.append(f"<p><em>[图表: {payload.get('title', '')}]</em></p>")
    parts.append("</main>\n</body>\n</html>")

    html = "\n".join(parts)
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
    return output_path


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ======================================================================
# PDF 渲染器 (reportlab, 封面/目录/页眉页脚 + Noto CJK + 高清图表)
# ======================================================================


def md_to_pdf(md_text: str, output_path: str | Path) -> str | Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.pdfmetrics import registerFontFamily
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from reportlab.platypus.tableofcontents import TableOfContents

    blocks = _parse_blocks(md_text)
    title = _extract_title(blocks)

    # ---- 注册中文字体 (TrueType 轮廓) ----
    reg, bold, sreg, sbold = _resolve_pdf_fonts()

    def _register(fname: str, path: str | None) -> str | None:
        try:
            if path and os.path.exists(path):
                if path.lower().endswith(".ttc"):
                    pdfmetrics.registerFont(TTFont(fname, path, subfontIndex=0))
                else:
                    pdfmetrics.registerFont(TTFont(fname, path))
                return fname
        except Exception:
            pass
        return None

    _sans = _register("CJKSans", reg) or "Helvetica"
    _sans_b = _register("CJKSansBold", bold) or _sans
    _serif = _register("CJKSerif", sreg) or _sans
    _serif_b = _register("CJKSerifBold", sbold) or _serif

    with contextlib.suppress(Exception):
        registerFontFamily("CJKSans", normal="CJKSans", bold="CJKSansBold", italic="CJKSans", boldItalic="CJKSansBold")
    with contextlib.suppress(Exception):
        registerFontFamily(
            "CJKSerif", normal="CJKSerif", bold="CJKSerifBold", italic="CJKSerif", boldItalic="CJKSerifBold"
        )

    # ---- 样式 ----
    styles = getSampleStyleSheet()
    body_font = "CJKSans" if "CJKSans" in pdfmetrics.getRegisteredFontNames() else "Helvetica"
    heading_font = body_font

    styles.add(
        ParagraphStyle(
            "CNTitle",
            fontName=heading_font,
            fontSize=26,
            leading=34,
            textColor=colors.white,
            alignment=1,
            spaceAfter=12,
        )
    )
    styles.add(
        ParagraphStyle(
            "CNH1",
            fontName=heading_font,
            fontSize=18,
            leading=26,
            textColor=colors.HexColor("#1e3a8a"),
            spaceBefore=18,
            spaceAfter=10,
        )
    )
    styles.add(
        ParagraphStyle(
            "CNH2",
            fontName=heading_font,
            fontSize=15,
            leading=22,
            textColor=colors.HexColor("#2563eb"),
            spaceBefore=14,
            spaceAfter=8,
        )
    )
    styles.add(
        ParagraphStyle(
            "CNH3",
            fontName=heading_font,
            fontSize=13,
            leading=20,
            textColor=colors.HexColor("#334155"),
            spaceBefore=10,
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            "CNBody", fontName=body_font, fontSize=10.5, leading=18, textColor=colors.HexColor("#334155"), spaceAfter=8
        )
    )
    styles.add(
        ParagraphStyle(
            "CNList",
            fontName=body_font,
            fontSize=10.5,
            leading=18,
            leftIndent=16,
            bulletIndent=4,
            spaceAfter=4,
            textColor=colors.HexColor("#334155"),
        )
    )
    styles.add(
        ParagraphStyle(
            "CNQuote",
            fontName=body_font,
            fontSize=10,
            leading=16,
            leftIndent=14,
            textColor=colors.HexColor("#64748b"),
            borderPadding=8,
            backColor=colors.HexColor("#f8fafc"),
            borderColor=colors.HexColor("#0ea5e9"),
            borderWidth=0,
            spaceAfter=10,
        )
    )

    # ---- 封面 / 页眉页脚回调 (局部函数, 闭包捕获 title/body_font) ----
    def _draw_cover(canvas: Any, doc_: Any) -> None:
        w, h = A4
        canvas.saveState()
        canvas.setFillColor(colors.HexColor("#1e3a8a"))
        canvas.rect(0, 0, w, h, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        bold_name = "CJKSansBold" if "CJKSansBold" in pdfmetrics.getRegisteredFontNames() else "Helvetica-Bold"
        canvas.setFont(bold_name, 30)
        canvas.drawCentredString(w / 2, h / 2 + 0.6 * cm, title)
        canvas.setFont(body_font, 13)
        canvas.drawCentredString(w / 2, h / 2 - 1.2 * cm, "Meerkat AI · 智能报告")
        canvas.restoreState()

    def _draw_header_footer(canvas: Any, doc_: Any) -> None:
        w, h = A4
        canvas.saveState()
        canvas.setFont(body_font, 8)
        canvas.setFillColor(colors.HexColor("#94a3b8"))
        canvas.drawString(2 * cm, h - 1.4 * cm, title)
        canvas.setStrokeColor(colors.HexColor("#e2e8f0"))
        canvas.line(2 * cm, h - 1.6 * cm, w - 2 * cm, h - 1.6 * cm)
        canvas.drawCentredString(w / 2, 1.0 * cm, f"— {doc_.page} —")
        canvas.restoreState()

    class DocTemplate(SimpleDocTemplate):  # type: ignore[misc]
        def afterFlowable(self, flowable: Any) -> None:
            if isinstance(flowable, Paragraph):
                st = flowable.style.name
                text = flowable.getPlainText()
                if st in ("CNH1", "CNH2", "CNH3"):
                    lvl = {"CNH1": 0, "CNH2": 1, "CNH3": 2}.get(st, 0)
                    key = f"h-{self.page}-{len(text)}"
                    self.canv.bookmarkPage(key)
                    self.canv.addOutlineEntry(text, key, level=lvl, closed=False)
                    self.notify("TOCEntry", (lvl, text, self.page, key))

    doc = DocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2.2 * cm,
        bottomMargin=2.2 * cm,
        title=title,
        author="Meerkat AI",
    )

    story = []
    # 封面独占第一页
    story.append(PageBreak())

    # 目录 (需 multiBuild)
    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle(name="TOC1", fontName=heading_font, fontSize=11, leading=18, leftIndent=6),
        ParagraphStyle(name="TOC2", fontName=heading_font, fontSize=10, leading=16, leftIndent=24),
        ParagraphStyle(name="TOC3", fontName=heading_font, fontSize=9, leading=14, leftIndent=42),
    ]
    story.append(Paragraph("目录", styles["CNH1"]))
    story.append(toc)
    story.append(PageBreak())

    # 正文
    for typ, payload in blocks:
        if typ == "heading":
            lvl, txt = payload
            st = {1: "CNH1", 2: "CNH2", 3: "CNH3"}.get(lvl, "CNH3")
            story.append(Paragraph(_inline_to_pdf(txt), styles[st]))
        elif typ == "para":
            story.append(Paragraph(_inline_to_pdf(payload), styles["CNBody"]))
        elif typ == "list":
            for item in payload:
                story.append(Paragraph(_inline_to_pdf(item), styles["CNList"], bulletText="•"))
        elif typ == "quote":
            story.append(Paragraph(_inline_to_pdf(payload), styles["CNQuote"]))
        elif typ == "math":
            asset = _math_assets(payload)
            width = asset["width"] * 72 / asset["dpi"]
            height = asset["height"] * 72 / asset["dpi"]
            scale = min(1.0, (15 * cm) / width) if width else 1.0
            image = Image(io.BytesIO(asset["png"]), width=width * scale, height=height * scale)
            image.hAlign = "CENTER"
            story.append(image)
        elif typ == "hr":
            story.append(Spacer(1, 8))
        elif typ == "code":
            story.append(Paragraph(_escape(payload), styles["CNQuote"]))
        elif typ == "image":
            _alt, _fmt, b64 = payload
            try:
                img = Image(io.BytesIO(base64.b64decode(b64)), width=14 * cm, height=10 * cm, kind="proportional")
                story.append(img)
            except Exception:
                story.append(Paragraph(_escape(_alt or "[图片]"), styles["CNBody"]))
        elif typ == "table":
            header, data = _parse_table_rows(payload)
            rows = ([header] if header else []) + data
            if rows:
                ncols = max(len(r) for r in rows)
                tdata = [
                    [Paragraph(_inline_to_pdf(r[ci] if ci < len(r) else ""), styles["CNBody"]) for ci in range(ncols)]
                    for r in rows
                ]
                t = Table(tdata, colWidths=[(17 * cm) / ncols] * ncols)
                style = [
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
                if header:
                    style.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2563eb")))
                t.setStyle(TableStyle(style))
                story.append(t)
        elif typ == "chart":
            png = _chart_to_png(payload)
            if png:
                try:
                    img = Image(io.BytesIO(png), width=15 * cm, height=9 * cm, kind="proportional")
                    story.append(img)
                except Exception:
                    story.append(Paragraph(_escape(payload.get("title", "[图表]")), styles["CNBody"]))
        story.append(Spacer(1, 4))

    doc.multiBuild(story, onFirstPage=_draw_cover, onLaterPages=_draw_header_footer)
    return output_path


def _escape(text: str) -> str:
    """转义 reportlab 的 XML 特殊字符。"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _inline_to_pdf(text: str) -> str:
    """Markdown 行内样式 → ReportLab Paragraph 标记与数学图片。"""
    text, math_images = _math_placeholders(text, "pdf")
    text = _escape(_strip_inline(text)) if text.count("**") % 2 else _escape(text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\(([^)]*)\)", r'<a href="\2">\1</a>', text)
    for token, image in math_images.items():
        text = text.replace(token, image)
    return text


# ======================================================================
# DOCX 渲染器 (增强样式 + 图表 PNG 嵌入)
# ======================================================================


def md_to_docx(md_text: str, output_path: str | Path) -> str | Path:
    from docx import Document
    from docx.shared import Inches, Pt, RGBColor

    doc = Document()
    # 标题颜色
    for typ, payload in _parse_blocks(md_text):
        if typ == "heading":
            level, text = payload
            h = doc.add_heading(_strip_inline(text), level=min(level, 4))
            for run in h.runs:
                run.font.color.rgb = RGBColor(0x1E, 0x3A, 0x8A)
        elif typ == "para":
            doc.add_paragraph(_strip_inline(payload))
        elif typ == "image":
            from docx.shared import Inches

            _alt, _fmt, b64 = payload
            try:
                doc.add_picture(io.BytesIO(base64.b64decode(b64)), width=Inches(5.5))
            except Exception:
                doc.add_paragraph(_alt or "[图片]")
        elif typ == "list":
            for item in payload:
                doc.add_paragraph(_strip_inline(item), style="List Bullet")
        elif typ == "quote":
            p = doc.add_paragraph(_strip_inline(payload))
            p.paragraph_format.left_indent = Inches(0.3)
            for run in p.runs:
                run.font.italic = True
                run.font.color.rgb = RGBColor(0x64, 0x74, 0x8B)
        elif typ == "code":
            p = doc.add_paragraph(payload)
            for run in p.runs:
                run.font.name = "Courier New"
                run.font.size = Pt(9)
        elif typ == "table":
            header, data = _parse_table_rows(payload)
            rows = ([header] if header else []) + data
            if rows:
                table = doc.add_table(rows=len(rows), cols=max(len(r) for r in rows))
                table.style = "Light Grid Accent 1"
                for ri, row in enumerate(rows):
                    for ci in range(len(table.columns)):
                        table.cell(ri, ci).text = row[ci] if ci < len(row) else ""
        elif typ == "chart":
            png = _chart_to_png(payload)
            if png:
                try:
                    doc.add_picture(io.BytesIO(png), width=Inches(6.0))
                except Exception:
                    doc.add_paragraph(payload.get("title", "[图表]"))
    doc.save(str(output_path))
    return output_path


# ======================================================================
# 统一入口
# ======================================================================

_FORMATS: dict[str, Callable[[str, str | Path], str | Path]] = {
    "html": md_to_html,
    "docx": md_to_docx,
    "pdf": md_to_pdf,
}


def convert(fmt: str, md_text: str, output_path: str | Path) -> str | Path:
    """统一转换入口: convert('html'|'docx'|'pdf', markdown_text, output_path)。"""
    fmt = fmt.lower().lstrip(".")
    if fmt not in _FORMATS:
        raise ValueError(f"不支持的格式: {fmt}, 支持: {list(_FORMATS)}")
    return _FORMATS[fmt](md_text, output_path)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("用法: python3 doc_tools.py <md2html|md2docx|md2pdf> <input.md> <output>")
        sys.exit(1)
    cmd, inp, outp = sys.argv[1], sys.argv[2], sys.argv[3]
    fmt = cmd.replace("md2", "")
    with open(inp, encoding="utf-8") as f:
        md = f.read()
    result = convert(fmt, md, outp)
    print(f"[done] {fmt} → {result}")
