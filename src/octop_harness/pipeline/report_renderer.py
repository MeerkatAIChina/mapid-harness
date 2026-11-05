#!/usr/bin/env python3
"""专业报告渲染器：离线 ECharts HTML + 可导航 Word，PDF 复用 doc_tools。"""

from __future__ import annotations

import base64
import io
import json
import os as _os
import re
from pathlib import Path
from typing import Any

from octop_harness.pipeline.charts import validate_chart
from octop_harness.pipeline.doc_tools import (
    _chart_to_png,
    _escape_html,
    _extract_title,
    _inline_to_html,
    _math_assets,
    _parse_blocks,
    _parse_table_rows,
    _strip_inline,
)
from octop_harness.pipeline.doc_tools import convert as legacy_convert

_BRAND = _os.environ.get("LONG_REPORT_BRAND", "Meerkat AI · 专业报告")

ASSETS = Path(__file__).parent / "assets"
PALETTE = ["#2F5D7C", "#4E8A75", "#C08A3E", "#8A6FA3", "#A85454", "#4D7EA8"]
CSS = r"""
:root{--primary:#2F5D7C;--text:#24313A;--muted:#667780;--border:#D9E1E6;--soft:#EEF3F6;--bg:#FAFBFC}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:16px/1.78 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans CJK SC","Microsoft YaHei",sans-serif}
.cover{padding:56px 6vw 38px;background:linear-gradient(135deg,#203C50,#2F5D7C);color:#F5F8FA}.cover h1{font-size:34px;line-height:1.3;margin:0 0 16px}.cover .subtitle{opacity:.88}.cover .meta{font-size:13px;opacity:.72}
.layout{max-width:1440px;margin:auto;display:grid;grid-template-columns:280px minmax(0,1fr);gap:32px;padding:32px 24px}
.toc{position:sticky;top:20px;align-self:start;max-height:calc(100vh - 40px);overflow:auto;background:#fff;border:1px solid var(--border);border-radius:12px;padding:18px}.toc h2{font-size:19px;margin:0 0 10px}.toc ul{list-style:none;padding:0;margin:0}.toc li{margin:2px 0}.toc a{display:block;color:var(--text);text-decoration:none;padding:5px 8px;border-radius:6px;font-size:14px;line-height:1.45}.toc a:hover,.toc a:focus{background:var(--soft);color:var(--primary)}.toc .lvl2{padding-left:20px;color:var(--muted);font-size:13px}
main{min-width:0;background:#fff;border:1px solid var(--border);border-radius:12px;padding:42px 48px}h1,h2,h3,h4{line-height:1.38;color:var(--primary);scroll-margin-top:20px}h2{font-size:26px;border-bottom:2px solid var(--primary);padding-bottom:8px;margin-top:42px}h3{font-size:21px;margin-top:32px}h4{font-size:18px}p{margin:12px 0}blockquote{margin:18px 0;padding:12px 18px;border-left:4px solid var(--primary);background:var(--soft);color:var(--muted)}pre{overflow:auto;padding:16px;background:#17242D;color:#E9F0F4;border-radius:8px;font-size:13px}code{font-family:"SFMono-Regular",Consolas,monospace}hr{border:0;border-top:1px solid var(--border);margin:32px 0}img{max-width:100%;height:auto}table{width:100%;border-collapse:collapse;margin:20px 0;font-size:14px;display:block;overflow-x:auto}th,td{border:1px solid var(--border);padding:8px 10px;text-align:left;vertical-align:top}th{background:var(--soft);color:var(--primary)}tr:nth-child(even) td{background:#FAFCFD}.figure{margin:24px 0;padding:16px;border:1px solid var(--border);border-radius:10px;background:#fff}.chart{width:100%;height:440px;min-height:320px}.chart-caption{text-align:center;color:var(--muted);font-size:13px;margin-top:8px}.formula{margin:20px 0;padding:16px 20px;text-align:center;font:600 18px/1.7 "Cambria Math","STIX Two Math","Noto Sans CJK SC",serif;color:var(--primary);background:var(--soft);border-radius:8px;overflow-wrap:anywhere}.inline-math{display:inline-block;vertical-align:middle;margin:0 1px;max-width:none}.formula img{display:inline-block;max-width:100%;height:auto}
@media(max-width:900px){.layout{display:block;padding:14px}.toc{position:relative;top:auto;max-height:none;margin-bottom:18px}main{padding:24px 20px}.cover{padding:36px 24px}.cover h1{font-size:28px}.chart{height:380px}}
@media print{body{background:#fff;font-size:10.5pt}.cover{padding:28px 32px}.cover h1{font-size:26px}.layout{display:block;padding:0}.toc{display:none}main{border:0;padding:0}h2,h3,h4{break-after:avoid}.figure,table,blockquote{break-inside:avoid}.chart{height:360px}nav{display:none}a{color:inherit;text-decoration:none}}
"""


def chart_option(chart: dict[str, Any]) -> dict[str, Any]:
    ctype = chart.get("type", "bar")
    cats = chart.get("categories") or []
    series = chart.get("series") or []
    if not series and ctype not in ("graph", "flow"):
        return {"title": {"text": chart.get("title") or "", "left": "center"}, "series": []}
    unit = chart.get("unit") or chart.get("y_label") or ""
    option: dict[str, Any] = {
        "color": PALETTE,
        "backgroundColor": "transparent",
        "title": {
            "text": chart.get("title") or "",
            "left": "center",
            "textStyle": {"fontSize": 16, "color": "#2F5D7C"},
        },
        "tooltip": {"trigger": "item" if ctype in ("pie", "donut", "funnel", "gauge") else "axis"},
        "legend": {"bottom": 0, "type": "scroll"},
        "grid": {"left": 60, "right": 28, "top": 58, "bottom": 64, "containLabel": True},
    }
    if ctype in ("pie", "donut"):
        option["series"] = [
            {
                "name": series[0].get("name", "数值") if series else "数值",
                "type": "pie",
                "radius": ["38%", "68%"] if ctype == "donut" else "68%",
                "center": ["50%", "54%"],
                "data": [{"name": c, "value": v} for c, v in zip(cats, series[0].get("data", []), strict=False)],
                "label": {"formatter": "{b}: {c} ({d}%)"},
            }
        ]
    elif ctype == "radar":
        option["radar"] = {"indicator": [{"name": c, "max": chart.get("max", 5)} for c in cats]}
        option["series"] = [
            {
                "name": "多方案对比",
                "type": "radar",
                "data": [
                    {"name": s.get("name", f"系列{i + 1}"), "value": s.get("data", []), "areaStyle": {"opacity": 0.12}}
                    for i, s in enumerate(series)
                ],
                "label": {"show": False},
            }
        ]
    elif ctype == "flow":
        option["tooltip"] = {"trigger": "item"}
        option["series"] = [
            {
                "type": "graph",
                "layout": "none",
                "roam": False,
                "symbol": "roundRect",
                "symbolSize": [125, 55],
                "edgeSymbol": ["none", "arrow"],
                "label": {"show": True, "fontSize": 12},
                "edgeLabel": {"show": False},
                "data": [
                    {"name": c, "x": i * 180, "y": 160, "itemStyle": {"color": PALETTE[i % len(PALETTE)]}}
                    for i, c in enumerate(cats)
                ],
                "links": [{"source": cats[i], "target": cats[i + 1]} for i in range(len(cats) - 1)],
            }
        ]
    elif ctype == "graph":
        # R-3 语义统一：以 matplotlib 预设坐标为准（validate 已强制 nodes 带 x/y），
        # ECharts 端改固定布局，不再 force 自动布局（两端同一语义）。
        option["tooltip"] = {"trigger": "item"}
        option["series"] = [
            {
                "type": "graph",
                "layout": "none",
                "roam": True,
                "symbol": "roundRect",
                "symbolSize": [105, 48],
                "edgeSymbol": ["none", "arrow"],
                "label": {"show": True, "fontSize": 11},
                "data": [
                    {
                        "name": n["name"],
                        "x": n.get("x", 0),
                        "y": n.get("y", 0),
                        "itemStyle": {"color": PALETTE[i % len(PALETTE)]},
                    }
                    for i, n in enumerate(chart.get("nodes", []))
                ],
                "links": [
                    {
                        "source": link["source"],
                        "target": link["target"],
                        "label": {"show": True, "formatter": link.get("label", ""), "fontSize": 9},
                    }
                    for link in chart.get("links", [])
                ],
            }
        ]
    elif ctype == "heatmap":
        values = [point[2] for point in chart.get("heat_data", [])]
        option["tooltip"] = {"trigger": "item"}
        option["xAxis"] = {"type": "category", "data": cats}
        option["yAxis"] = {"type": "category", "data": [s.get("name", "") for s in series]}
        option["visualMap"] = {
            "min": min(values),
            "max": max(values),
            "calculable": True,
            "orient": "horizontal",
            "left": "center",
            "bottom": 0,
            "inRange": {"color": ["#EEF3F6", "#73A6B8", "#2F5D7C"]},
        }
        option["series"] = [
            {
                "type": "heatmap",
                "data": chart.get("heat_data", []),
                "label": {"show": True},
                "emphasis": {"itemStyle": {"shadowBlur": 8}},
            }
        ]
    elif ctype == "scatter":
        option["tooltip"] = {"trigger": "item"}
        option["xAxis"] = {"type": "value", "name": chart.get("x_label", "")}
        option["yAxis"] = {"type": "value", "name": chart.get("y_label", "")}
        option["series"] = [
            {
                "name": s.get("name", "方案"),
                "type": "scatter",
                "data": s.get("data", []),
                "symbolSize": 18,
                "label": {"show": True, "formatter": "{@[3]}", "position": "top"},
            }
            for s in series
        ]
    elif ctype == "funnel":
        option["series"] = [
            {
                "name": series[0].get("name", "数值") if series else "数值",
                "type": "funnel",
                "left": "12%",
                "width": "72%",
                "sort": "descending",
                "data": [{"name": c, "value": v} for c, v in zip(cats, series[0].get("data", []), strict=False)],
                "label": {"show": True, "position": "inside"},
            }
        ]
    elif ctype == "gauge":
        value = series[0].get("data", [0])[0] if series and series[0].get("data") else 0
        option["series"] = [
            {
                "name": chart.get("title") or "指标",
                "type": "gauge",
                "min": chart.get("min", 0),
                "max": chart.get("max", 100),
                "progress": {"show": True},
                "detail": {"formatter": "{value}" + unit},
                "data": [{"value": value, "name": chart.get("title") or "指标"}],
            }
        ]
    else:
        x = {"type": "category", "data": cats, "axisLabel": {"interval": 0, "rotate": 24 if len(cats) > 6 else 0}}
        y = {"type": "value", "name": unit}
        if ctype == "barh":
            x, y = y, x
        option["xAxis"] = x
        option["yAxis"] = y
        e_type = "bar" if ctype in ("bar", "barh") else "line"
        option["series"] = [
            {
                "name": s.get("name", f"系列{i + 1}"),
                "type": e_type,
                "data": s.get("data", []),
                "smooth": ctype in ("line", "area"),
                "areaStyle": {"opacity": 0.18} if ctype == "area" else None,
                "label": {"show": True, "position": "top" if ctype in ("bar", "barh") else "outside"},
                "symbolSize": 8 if ctype == "scatter" else 6,
            }
            for i, s in enumerate(series)
        ]
        option["series"] = [{k: v for k, v in s.items() if v is not None} for s in option["series"]]
    return option


def md_to_html(md_text: str, output_path: str | Path) -> str | Path:
    blocks = _parse_blocks(md_text)
    title = _extract_title(blocks)
    toc = [(p[0], _strip_inline(p[1])) for t, p in blocks if t == "heading" and p[0] <= 2]
    parts = [
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{_escape_html(title)}</title><style>{CSS}</style></head><body>",
        f'<header class="cover"><h1>{_escape_html(title)}</h1><p class="subtitle">{_escape_html(_BRAND)}</p><span class="meta">自包含 HTML · 离线交互图表</span></header>',
        '<div class="layout">',
    ]
    if len(toc) >= 3:
        parts.append('<nav class="toc" aria-label="目录"><h2>目录</h2><ul>')
        for i, (lvl, txt) in enumerate(toc):
            cls = "lvl2" if lvl == 2 else ""
            parts.append(f'<li><a class="{cls}" href="#sec-{i}">{_escape_html(txt)}</a></li>')
        parts.append("</ul></nav>")
    parts.append("<main>")
    h_idx = 0
    chart_count = 0
    for typ, payload in blocks:
        if typ == "heading":
            lvl, txt = payload
            anchor = f"sec-{h_idx}" if lvl <= 2 else ""
            if anchor:
                h_idx += 1
            parts.append(
                f"<h{min(lvl, 6)}{' id="' + anchor + '"' if anchor else ''}>{_inline_to_html(txt)}</h{min(lvl, 6)}>"
            )
        elif typ == "para":
            parts.append(f"<p>{_inline_to_html(payload)}</p>")
        elif typ == "list":
            parts.append("<ul>" + "".join(f"<li>{_inline_to_html(x)}</li>" for x in payload) + "</ul>")
        elif typ == "quote":
            parts.append(f"<blockquote>{_inline_to_html(payload)}</blockquote>")
        elif typ == "math":
            asset = _math_assets(payload)
            parts.append(f'<div class="formula" role="math" aria-label="{_escape_html(payload)}">{asset["svg"]}</div>')
        elif typ == "hr":
            parts.append("<hr>")
        elif typ == "code":
            parts.append(f"<pre><code>{_escape_html(payload)}</code></pre>")
        elif typ == "image":
            alt, fmt, b64 = payload
            parts.append(f'<img src="data:image/{fmt};base64,{b64}" alt="{_escape_html(alt)}">')
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
            chart_count += 1
            cid = f"chart-{chart_count}"
            title_text = _escape_html(payload.get("title") or f"图表 {chart_count}")
            parts.append(
                f'<figure class="figure"><div id="{cid}" class="chart" role="img" aria-label="{title_text}"></div><figcaption class="chart-caption">{title_text}</figcaption></figure>'
            )
    parts.append("</main></div>")
    bundle = ASSETS / "echarts.min.js"
    if chart_count and bundle.exists():
        parts.append(f"<script>{bundle.read_text(encoding='utf-8')}</script>")
        options = [chart_option(payload) for typ, payload in blocks if typ == "chart"]
        parts.append(
            f"<script>document.addEventListener('DOMContentLoaded',function(){{const options={json.dumps(options, ensure_ascii=False)};document.querySelectorAll('.chart').forEach(function(el,i){{const c=echarts.init(el,null,null,{{renderer:'canvas'}});c.setOption(options[i]);window.addEventListener('resize',function(){{c.resize()}});}});}});</script>"
        )
    parts.append("</body></html>")
    html = "\n".join(parts)
    Path(output_path).write_text(html, encoding="utf-8")
    return output_path


def md_to_docx(md_text: str, output_path: str | Path) -> str | Path:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Inches, Pt, RGBColor

    blocks = _parse_blocks(md_text)
    title = _extract_title(blocks)
    doc = Document()
    section = doc.sections[0]
    section.top_margin = section.bottom_margin = Cm(2.0)
    section.left_margin = section.right_margin = Cm(2.19)
    normal = doc.styles["Normal"]
    normal.font.name = "Noto Sans CJK SC"
    normal.font.size = Pt(10.5)
    normal.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "Noto Sans CJK SC")
    normal.paragraph_format.line_spacing = 1.5
    normal.paragraph_format.space_after = Pt(6)
    for level in range(1, 5):
        style = doc.styles[f"Heading {level}"]
        style.font.name = "Noto Sans CJK SC"
        style.font.color.rgb = RGBColor(0x2F, 0x5D, 0x7C)
        style.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "Noto Sans CJK SC")
    header = section.header.paragraphs[0]
    header.text = title
    header.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    footer._p.append(fld)
    doc.add_heading(title, 0)
    doc.add_paragraph(_os.environ.get("LONG_REPORT_AUDIENCE", "专业研究报告"))
    doc.add_paragraph(f"{_BRAND} · 同源文档与图表")
    doc.add_page_break()  # type: ignore[no-untyped-call]
    toc_headings = [(p[0], _strip_inline(p[1])) for t, p in blocks if t == "heading" and p[0] <= 2]
    if toc_headings:
        doc.add_heading("目录", level=1)
        for i, (lvl, text) in enumerate(toc_headings):
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.6 if lvl == 1 else 1.2)
            link = OxmlElement("w:hyperlink")
            link.set(qn("w:anchor"), f"sec-{i}")
            run = OxmlElement("w:r")
            text_el = OxmlElement("w:t")
            text_el.text = text
            run.append(text_el)
            link.append(run)
            p._p.append(link)
        doc.add_page_break()  # type: ignore[no-untyped-call]

    def add_text_with_math(paragraph: Any, text: str) -> None:
        parts = re.split(r"(?<!\$)\$(?!\$)([^$]+?)\$(?!\$)", text, flags=re.S)
        for index, part in enumerate(parts):
            if index % 2 == 0:
                if part:
                    paragraph.add_run(_strip_inline(part))
                continue
            asset = _math_assets(part)
            run = paragraph.add_run()
            run.add_picture(
                io.BytesIO(asset["png"]),
                width=Inches(asset["width"] / asset["dpi"]),
                height=Inches(asset["height"] / asset["dpi"]),
            )

    h_idx = 0
    for typ, payload in blocks:
        if typ == "heading":
            lvl, txt = payload
            h = doc.add_heading(_strip_inline(txt), level=min(lvl, 4))
            if lvl <= 2:
                bid = 1000 + h_idx
                start = OxmlElement("w:bookmarkStart")
                start.set(qn("w:id"), str(bid))
                start.set(qn("w:name"), f"sec-{h_idx}")
                end = OxmlElement("w:bookmarkEnd")
                end.set(qn("w:id"), str(bid))
                h._p.insert(1, start)
                h._p.append(end)
                h_idx += 1
        elif typ == "para":
            p = doc.add_paragraph()
            add_text_with_math(p, payload)
            p.paragraph_format.first_line_indent = Cm(0.74)
        elif typ == "list":
            for item in payload:
                p = doc.add_paragraph(style="List Bullet")
                add_text_with_math(p, item)
        elif typ == "quote":
            p = doc.add_paragraph()
            add_text_with_math(p, payload)
            p.paragraph_format.left_indent = Cm(0.8)
            for run in p.runs:
                run.font.italic = True
                run.font.color.rgb = RGBColor(0x66, 0x77, 0x80)
        elif typ == "math":
            asset = _math_assets(payload)
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = p.add_run()
            width = asset["width"] / asset["dpi"]
            height = asset["height"] / asset["dpi"]
            scale = min(1.0, 5.8 / width) if width else 1.0
            run.add_picture(io.BytesIO(asset["png"]), width=Inches(width * scale), height=Inches(height * scale))
        elif typ == "code":
            p = doc.add_paragraph(payload)
            for run in p.runs:
                run.font.name = "Courier New"
                run.font.size = Pt(8.5)
        elif typ == "hr":
            doc.add_paragraph("—" * 24)
        elif typ == "image":
            alt, _fmt, b64 = payload
            try:
                doc.add_picture(io.BytesIO(base64.b64decode(b64)), width=Inches(5.8))
            except Exception:
                doc.add_paragraph(alt or "[图片]")
        elif typ == "table":
            header, data = _parse_table_rows(payload)
            rows = ([header] if header else []) + data
            if rows:
                table = doc.add_table(rows=len(rows), cols=max(len(r) for r in rows))
                table.style = "Light Grid Accent 1"
                table.autofit = True
                for ri, row in enumerate(rows):
                    for ci in range(len(table.columns)):
                        cell = table.cell(ri, ci)
                        p = cell.paragraphs[0]
                        if ci < len(row):
                            add_text_with_math(p, row[ci])
                        for run in p.runs:
                            run.font.size = Pt(8.5)
        elif typ == "chart":
            png = _chart_to_png(payload)
            if png:
                doc.add_picture(io.BytesIO(png), width=Inches(6.0))
                caption = doc.add_paragraph(payload.get("title") or "数据图表")
                caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
                for run in caption.runs:
                    run.font.size = Pt(9)
                    run.font.color.rgb = RGBColor(0x66, 0x77, 0x80)
    doc.save(str(output_path))
    return output_path


def convert(fmt: str, md_text: str, output_path: str | Path, ledger_refs: set[int] | None = None) -> str | Path:
    # 图表契约闸门（终审修复 2b）：任一 chart 块不过 validate_chart 即拒建，
    # 缺 title/rationale/refs 的图表不得进入三格式产物。
    errors: list[str] = []
    for i, (typ, payload) in enumerate(_parse_blocks(md_text), start=1):
        if typ == "chart":
            errors.extend(f"第 {i} 块：{e}" for e in validate_chart(payload, ledger_refs))
    if errors:
        raise ValueError("图表契约校验未通过：\n" + "\n".join(errors))
    if fmt == "html":
        return md_to_html(md_text, output_path)
    if fmt == "docx":
        return md_to_docx(md_text, output_path)
    return legacy_convert(fmt, md_text, output_path)
