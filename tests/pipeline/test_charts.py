"""语义图表工具测试（设计文档_R4 §6.2 / 执行计划 Task 13）。"""

from __future__ import annotations

import pytest

pytest.importorskip("matplotlib", reason="pipeline extras 未安装")

from octop_harness.pipeline.charts import validate_chart
from octop_harness.pipeline.report_renderer import chart_option

GOOD_BAR = {
    "type": "bar",
    "title": "欧普份额领先",
    "rationale": "支撑龙头判断",
    "categories": ["欧普", "雷士"],
    "series": [{"name": "份额", "data": [39.3, 8.1]}],
    "refs": [3],
}


def test_chart_rejected_without_rationale():
    chart = {**GOOD_BAR}
    del chart["rationale"]
    errors = validate_chart(chart, {3})
    assert any("rationale" in e for e in errors)


def test_chart_rejected_without_ledger_ref():
    chart = {**GOOD_BAR}
    del chart["refs"]
    errors = validate_chart(chart, {3})
    assert any("台账" in e or "ref" in e.lower() for e in errors)


def test_chart_rejected_unregistered_ref():
    errors = validate_chart(GOOD_BAR, {9})
    assert any("未登记" in e or "注册" in e for e in errors)


def test_chart_accepted_full_contract():
    assert validate_chart(GOOD_BAR, {3}) == []


def test_chart_legacy_ledger_exempt():
    # 台账无 ref 列（None）→ 不查注册，但 refs 字段仍必须存在
    assert validate_chart(GOOD_BAR, None) == []
    chart = {**GOOD_BAR}
    del chart["refs"]
    assert validate_chart(chart, None) != []


def test_graph_uses_preset_coordinates():
    chart = {
        "type": "graph",
        "title": "竞争关系",
        "rationale": "展示竞争拓扑",
        "refs": [3],
        "nodes": [
            {"name": "欧普", "x": 0, "y": 0},
            {"name": "雷士", "x": 100, "y": 80},
        ],
        "links": [{"source": "欧普", "target": "雷士"}],
    }
    assert validate_chart(chart, {3}) == []
    option = chart_option(chart)
    assert option["series"][0]["layout"] == "none"  # 预设坐标，非 force
    assert option["series"][0]["data"][0]["x"] == 0


def test_graph_rejected_without_coordinates():
    chart = {
        "type": "graph",
        "title": "t",
        "rationale": "r",
        "refs": [3],
        "nodes": [{"name": "a"}, {"name": "b"}],
        "links": [{"source": "a", "target": "b"}],
    }
    assert validate_chart(chart, {3}) != []


def test_heatmap_rejected_without_heat_data():
    """缺 heat_data 的热力图能过校验，但 HTML 渲染对空列表求 min/max 崩溃（终审复现）。"""
    chart = {
        "type": "heatmap",
        "title": "t",
        "rationale": "r",
        "refs": [3],
        "categories": ["a", "b"],
        "series": [{"name": "s", "data": [1, 2]}],
    }
    assert any("heat_data" in e for e in validate_chart(chart, {3}))


def test_heatmap_accepted_with_heat_data():
    chart = {
        "type": "heatmap",
        "title": "t",
        "rationale": "r",
        "refs": [3],
        "categories": ["a", "b"],
        "series": [{"name": "s", "data": [1, 2]}],
        "heat_data": [[0, 0, 1], [1, 0, 2]],
    }
    assert validate_chart(chart, {3}) == []
