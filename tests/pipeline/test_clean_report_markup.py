"""残留清理工具测试（设计文档_R4 §5.1 / 执行计划 Task 7）。"""

from __future__ import annotations

import pytest

from octop_harness.pipeline.clean_report_markup import (
    FORBIDDEN_PATTERNS,
    clean_text,
    scan_text,
)

CLEAN_BODY = "欧普照明 2024 年营收 70.96 亿元，同比增长 5.2%。\n" * 3


@pytest.mark.parametrize(
    "residue",
    [
        "原文描述有误，已修正为最新口径。",
        "建议改为更严谨的表述。",
        "本节要点 (Deep Analysis) 如下。",
        "(New Case) 欧普照明案例。",
        "Start from the cut-off: continue the section.",
        "Here's a thinking process: first analyze the input.",
    ],
)
def test_forbidden_patterns_hit(residue: str):
    assert scan_text(residue), f"未命中残留：{residue}"


def test_clean_text_untouched():
    assert scan_text(CLEAN_BODY) == []
    cleaned, stats = clean_text(CLEAN_BODY)
    assert cleaned == CLEAN_BODY
    assert stats["removed"] == 0


def test_clean_removes_residue_lines():
    text = CLEAN_BODY + "原文描述有误，已修正。\n"
    cleaned, stats = clean_text(text)
    assert "原文描述有误" not in cleaned
    assert stats["removed"] == 1


def test_fence_content_protected():
    # 代码围栏内的内容即使像残留也不动（chart/代码不是生成残留）
    text = "正文。\n```chart\nStart from the cut-off: fake\n```\n"
    cleaned, stats = clean_text(text)
    assert "Start from the cut-off" in cleaned
    assert stats["removed"] == 0


def test_every_pattern_has_hit():
    # 清单里每个模式都必须有实证命中样例（防空模式摆设）
    samples = {
        "原文描述有误": "原文描述有误，已修正。",
        "建议改为": "建议改为更严谨表述。",
        "Deep Analysis": "(Deep Analysis)",
        "New Case": "(New Case)",
        "cut-off": "Start from the cut-off: x",
        "thinking process": "Here's a thinking process:",
    }
    for p in FORBIDDEN_PATTERNS:
        assert any(p.search(s) for s in samples.values()), f"空模式：{p.pattern}"
