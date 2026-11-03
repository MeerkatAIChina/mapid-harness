"""三格式同源渲染冒烟测试（设计文档_R4 §6.1 / 执行计划 Task 11）。"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("matplotlib", reason="pipeline extras 未安装")
pytest.importorskip("docx", reason="pipeline extras 未安装")
pytest.importorskip("reportlab", reason="pipeline extras 未安装")

from octop_harness.pipeline.report_renderer import convert
from octop_harness.pipeline.validate_report import validate_report

SAMPLE_MD = """# 测试报告

## 第一章 概述

欧普照明 2024 年营收 70.96 亿元[1]，同比增长 5.2%。

| 企业 | 份额 |
|---|---|
| 欧普 | 39.3%[1] |
| 雷士 | 8.1%[1] |

> 数据来源：[1]

公式示例：$$E=mc^2$$

```chart
{"type": "bar", "title": "欧普份额领先", "rationale": "支撑龙头判断", "categories": ["欧普", "雷士"], "series": [{"name": "份额", "data": [39.3, 8.1]}], "refs": [1]}
```

## 本章参考资料

[1] 欧普照明《2024年年度报告》，2025，https://example.com
"""


def test_convert_three_formats_from_same_md(tmp_path: Path):
    for fmt in ("html", "docx", "pdf"):
        out = tmp_path / f"report.{fmt}"
        convert(fmt, SAMPLE_MD, str(out))
        assert out.is_file() and out.stat().st_size > 1000, f"{fmt} 输出异常"


def test_validate_report_passes_on_full_outputs(tmp_path: Path):
    from octop_harness.pipeline.build_outputs import build_outputs

    md_path = tmp_path / "in.md"
    md_path.write_text(SAMPLE_MD, encoding="utf-8")
    build_outputs(md_path, tmp_path, "report")
    assert validate_report(tmp_path, "report") == []


def test_validate_report_catches_missing(tmp_path: Path):
    failures = validate_report(tmp_path, "report")
    assert any("缺" in f for f in failures)
