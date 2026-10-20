"""聚合所有检查器的 findings 为结构化 JSON 报告。"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from octop_harness.report_qa.checks import run_all_checks
from octop_harness.report_qa.constants import SEVERITY_HIGH


def build_report(workspace: Path) -> dict[str, Any]:
    """聚合 findings → {summary, high_risk_data, findings}。"""
    workspace = workspace.resolve()
    findings = run_all_checks(workspace)
    by_check: Counter[str] = Counter(f["check"] for f in findings)
    by_severity: Counter[str] = Counter(f["severity"] for f in findings)
    high_risk_data = [
        {"file": f["file"], "line": f["line"], "evidence": f["evidence"]}
        for f in findings
        if f["check"] == "unsourced_precise"
    ]
    return {
        "summary": {
            "total_findings": len(findings),
            "by_check": dict(sorted(by_check.items())),
            "by_severity": dict(sorted(by_severity.items())),
        },
        "high_risk_data": high_risk_data,
        "findings": findings,
    }


def format_human(result: dict[str, Any]) -> str:
    """人类可读摘要（打印到 stdout）。"""
    summary = result["summary"]
    lines = [
        "报告质量检查摘要",
        f"  发现总数: {summary['total_findings']}",
        "  按检查器:",
    ]
    for check, count in summary["by_check"].items():
        lines.append(f"    {check}: {count}")
    lines.append("  按严重度:")
    for severity, count in summary["by_severity"].items():
        lines.append(f"    {severity}: {count}")
    lines.append(f"  高危数据（无来源精确数值）: {len(result['high_risk_data'])} 条")
    high = [f for f in result["findings"] if f["severity"] == SEVERITY_HIGH]
    if high:
        lines.append("  高严重度发现（前 10 条）:")
        for f in high[:10]:
            lines.append(f"    [{f['check']}] {f['file']}:{f['line']} {f['message']}")
    return "\n".join(lines)
