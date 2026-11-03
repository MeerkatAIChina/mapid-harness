"""语义图表校验（设计文档_R4 §6.2，移植同事 enrich_charts.validate 并收紧）。

比同事严的三条（我们的数据契约）：
1. ``rationale`` 必须显式给出（同事版会从 note/title 自动兜底——我们不允许，
   「一句话如何帮助理解」是禁装饰图的核心闸门）；
2. ``refs`` 字段必填：图表数据必须来自台账条目（带 ref 编号），不允许章节文本现编；
3. 台账启用 ref 注册中心时（ledger_refs 非 None），refs 逐条核对已登记。
"""

from __future__ import annotations

from typing import Any

ALLOWED = {
    "bar", "barh", "line", "area", "pie", "donut", "radar", "scatter",
    "funnel", "gauge", "flow", "graph", "heatmap",
}


def _numeric(values: Any) -> bool:
    return isinstance(values, list) and bool(values) and all(
        isinstance(v, (int, float)) and not isinstance(v, bool) for v in values
    )


def _validate_structure(chart: dict) -> list[str]:
    """13 类型结构校验（同事 validate 的判定部分，异常改为错误列表）。"""
    errors: list[str] = []
    ctype = chart.get("type")
    if ctype not in ALLOWED:
        return [f"图表类型非法：{ctype!r}（允许 {sorted(ALLOWED)}）"]
    cats = chart.get("categories")
    series = chart.get("series")
    if ctype not in ("graph",) and not isinstance(cats, list):
        errors.append("categories 必须是字符串数组")
    if ctype not in ("graph",) and (not isinstance(series, list) or not series):
        errors.append("series 必须非空")
    if errors:
        return errors
    for item in series or []:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            errors.append("series 项必须包含名称")
            continue
        data = item.get("data")
        if ctype == "scatter":
            if not isinstance(data, list) or not data:
                errors.append("散点数据为空")
                continue
            for point in data:
                if not isinstance(point, list) or len(point) < 2 or not _numeric(point[:2]):
                    errors.append("散点必须为 [x,y,...] 数值点")
                    break
        elif ctype not in ("graph",) and not _numeric(data):
            errors.append("图表数据为空或不是数值数组")
        if ctype not in ("gauge", "scatter", "graph") and cats and len(cats) != len(data or []):
            errors.append(f"categories/data 长度不一致 {len(cats)} != {len(data or [])}")
    if ctype == "flow" and len(cats or []) < 2:
        errors.append("流程图至少需要两个阶段")
    if ctype == "radar" and len(cats or []) < 3:
        errors.append("雷达图至少需要三个维度")
    if ctype == "graph":
        nodes = chart.get("nodes")
        links = chart.get("links")
        if not isinstance(nodes, list) or len(nodes) < 2 or not isinstance(links, list):
            errors.append("关系图缺少 nodes/links")
            return errors
        names = {n.get("name") for n in nodes if isinstance(n, dict)}
        if len(names) != len(nodes) or "" in names:
            errors.append("关系图节点名称缺失或重复")
        for node in nodes:
            if not all(isinstance(node.get(k), (int, float)) for k in ("x", "y")):
                errors.append("关系图节点缺少预设布局坐标 x/y（语义以 matplotlib 预设坐标为准）")
                break
        for link in links:
            if link.get("source") not in names or link.get("target") not in names:
                errors.append("关系图连线引用了不存在的节点")
                break
    return errors


def validate_chart(chart_json: dict, ledger_refs: set[int] | None) -> list[str]:
    """返回错误信息列表；空列表 = 通过。ledger_refs=None 表示台账无 ref 列（豁免注册核对）。"""
    if not isinstance(chart_json, dict):
        return ["图表必须是对象"]
    errors = _validate_structure(chart_json)
    # 三要素闸门：title（表结论）+ rationale（显式，不兜底）+ 台账数据源
    if not chart_json.get("title"):
        errors.append("缺 title（图表标题须表结论）")
    if not chart_json.get("rationale"):
        errors.append("缺 rationale（一句话说明图表如何帮助理解，禁装饰图闸门）")
    refs = chart_json.get("refs")
    if not isinstance(refs, list) or not refs:
        errors.append("缺 refs：图表数据必须来自台账条目（带 ref 编号）")
    elif ledger_refs is not None:
        unregistered = [r for r in refs if r not in ledger_refs]
        if unregistered:
            errors.append(f"refs 含未登记编号：{unregistered}（须先在台账 ref 列取号）")
    return errors
