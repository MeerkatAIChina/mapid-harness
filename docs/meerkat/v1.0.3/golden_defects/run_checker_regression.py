"""金缺陷回归（检查器层）：复制基线 workspace → 注入 text_injectable 缺陷 → 跑 report_qa → 评分。

用法：
    python run_checker_regression.py --workspace <基线agent workspace> --out <输出目录>

产出：
    <out>/regression_ws/          带雷 workspace（chapters/ + progress.md + data_ledger.md）
    <out>/checker_ground_truth.json   注入点标注（defect_id / file / line）
    <out>/checker_findings.json       检查器原始发现
    终端打印：逐缺陷命中表 + 待裁决 FP 候选清单

评分口径（与执行计划 T1 咬合）：
    - 应检出缺陷 = manifest 中 detector 含 "code" 的缺陷（含 META 型，由原生 workspace 状态覆盖）；
    - 命中 = 同章节文件 + 对应检查器类型的 finding（注入雷允许行号偏移，原生雷按类级映射）；
    - FP 候选 = 既不对应注入雷也不对应原生雷的 finding，需人工裁决。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

from octop_harness.report_qa.report import build_report

# 检查器 → 可检出的金缺陷（类级映射，原生雷与注入雷共用）
CHECKER_TO_DEFECTS: dict[str, list[str]] = {
    "degeneration": ["GD-11"],
    "word_count": ["GD-13", "GD-14"],
    "ledger_schema": ["GD-15"],
    "ledger_freshness": ["GD-15"],
    "unsourced_precise": ["GD-06"],
    "numeric_consistency": ["GD-07", "GD-09"],
    "figure_companion": ["GD-17"],
    "structure": ["GD-12", "GD-16"],
    "conclusion_source": ["GD-19", "GD-20"],
}

DUP_PARA = "本段为逐字重复测试段落：双碳政策推动照明行业能效升级，节能改造工程加速落地。（注入用重复段）"

WORKSPACE_FILES = ("progress.md", "data_ledger.md", "outline.md")

# META 型状态伪造：GD-07/09 的代码检出路径是台账内查重（check_numeric_consistency），
# 需要台账里同时存在冲突数值——这正是 v3 结构化台账世界的状态。基线台账是 v2 自由
# 格式且停更，回归时补一张合规表把冲突值造进去。GD-08（份额倒挂）检查器明确
# 「倒挂检测暂不做」（T1 决策），不伪造，记为文档化缺口。
FORGED_LEDGER_TABLE = """
| 指标名 | 数值 | 单位 | 口径 | 来源 | 首现章节 |
|---|---|---|---|---|---|
| 雷士照明2024年营收 | 22 | 亿元 | 2024年报 | 雷士照明2024年报 | 05 |
| 雷士照明2024年营收 | 222.9 | 亿元 | 2024年报 | 雷士国际2024年报 | 09 |
| 木林森2024营收同比增速 | -0.35% | % | 2024年报 | 木林森2024年报 | 05 |
| 木林森2024营收同比增速 | -3.57% | % | 2024年报实算（175.36→169.10亿） | 木林森2024年报 | 09 |
| 欧普照明营业收入 | 77.95 | 亿元 | 2023年报 | 欧普照明2023年报 | 03 |
| 欧普照明营业收入 | 70.96 | 亿元 | 2024年报 | 欧普照明2024年报 | 03 |
| 欧普照明营业收入 | 69.70 | 亿元 | 2025年报 | 欧普照明2025年报 | 03 |
| 欧普照明上市时间 | 2016 年 6 月 | — | — | 招股说明书 | 03 |
| 欧普照明上市时间 | 2016 年 | — | — | 招股说明书 | 03 |
"""
# GD-18 阴性行：numeric_consistency 对这些指标名的任何命中均为违规（误报回归）
NEGATIVE_LEDGER_MARKERS = ("欧普照明营业收入", "欧普照明上市时间")
FORGED_LEDGER_DEFECTS = {"GD-07", "GD-09"}


def build_regression_ws(workspace: Path, manifest: dict, out: Path) -> list[dict]:
    """复制基线 workspace 并注入文本缺陷，返回 ground truth。"""
    ws = out / "regression_ws"
    if ws.exists():
        shutil.rmtree(ws)
    (ws / "chapters").mkdir(parents=True)
    chapters = sorted((workspace / "chapters").glob("*.md"))
    if not chapters:
        raise SystemExit(f"基线 workspace 无章节：{workspace / 'chapters'}")
    for ch in chapters:
        shutil.copy2(ch, ws / "chapters" / ch.name)
    for name in WORKSPACE_FILES:
        src = workspace / name
        if src.is_file():
            shutil.copy2(src, ws / name)

    # 伪造 GD-07/09 的台账状态（META 型）：冲突数值写入合规台账表
    ledger = ws / "data_ledger.md"
    ledger.write_text(
        ledger.read_text(encoding="utf-8").rstrip() + "\n" + FORGED_LEDGER_TABLE,
        encoding="utf-8",
    )
    ledger_lines = ledger.read_text(encoding="utf-8").splitlines()
    ledger_truth = []
    for i, line in enumerate(ledger_lines, start=1):
        if "雷士照明2024年营收" in line:
            ledger_truth.append({"defect_id": "GD-07", "file": "data_ledger.md", "line": i})
        if "木林森2024营收同比增速" in line:
            ledger_truth.append({"defect_id": "GD-09", "file": "data_ledger.md", "line": i})

    truth: list[dict] = list(ledger_truth)
    k = 0
    for d in manifest["text_injectable"]:
        snippet = d["snippet"]
        if snippet.startswith("__META__"):
            continue  # META 型由原生 workspace 状态覆盖
        if snippet == "__DUP_PARA__":
            snippet = DUP_PARA
            targets = [chapters[k % len(chapters)], chapters[(k + 1) % len(chapters)]]
        elif d.get("target"):
            targets = [c for c in chapters if d["target"] in c.name]
            if not targets:
                raise SystemExit(f"manifest {d['id']} target={d['target']!r} 无匹配章节")
        else:
            targets = [chapters[k % len(chapters)]]
        for target in targets:
            f = ws / "chapters" / target.name
            text = f.read_text(encoding="utf-8")
            line_start = text.count("\n") + 3  # 接两个换行后追加
            f.write_text(f"{text.rstrip()}\n\n{snippet}\n", encoding="utf-8")
            truth.append(
                {
                    "defect_id": d["id"],
                    "file": f"chapters/{target.name}",
                    "line": line_start,
                    "span_text": snippet[:40],
                }
            )
        k += 1
    return truth


def _native_hit_defects(f: dict, mapped: list[str]) -> list[str]:
    """原生雷归属：structure 类按 message 细分（日期 → GD-16，重复 → GD-12）。"""
    if f["check"] == "structure":
        if "日期" in f["message"]:
            return [d for d in mapped if d == "GD-16"]
        if "重复" in f["message"]:
            return [d for d in mapped if d == "GD-12"]
    return mapped


def score(findings: list[dict], truth: list[dict], manifest: dict) -> dict:
    """逐缺陷判定命中 + 逐 finding 判定 TP/FP 候选。

    TP 判定（提醒 #2）：注入命中（ground_truth）∪ 原生命中（类级映射——宿主
    基线本身携带 GD-06/11/12/13/14/15/16 的原生实例，命中它们同样是 TP）。
    FP 候选 = 不属于任何缺陷类的 finding（默认只有 figure_companion——它不对应
    金缺陷，需人工裁决是「真实但非金缺陷」还是误报）。
    """
    expected = [d["id"] for d in manifest["text_injectable"] if "code" in d["detector"]]

    # 1. 注入雷：同文件 + 同检查器类型（行号允许偏移）
    hit_by_defect: dict[str, list[dict]] = {d: [] for d in expected}
    for t in truth:
        defect = t["defect_id"]
        if defect not in hit_by_defect:
            continue
        for f in findings:
            if f["file"] == t["file"] and defect in CHECKER_TO_DEFECTS.get(f["check"], []):
                hit_by_defect[defect].append(f)

    # 2. 原生雷：类级映射的 finding 全部计为原生 TP 实例
    native_instance_count = 0
    for f in findings:
        mapped = CHECKER_TO_DEFECTS.get(f["check"], [])
        if not mapped:
            continue
        native_instance_count += 1
        for d in _native_hit_defects(f, mapped):
            if d in hit_by_defect and not hit_by_defect[d]:
                hit_by_defect[d].append(f)

    # 3. FP 候选：不对应任何缺陷类的 finding
    fp_candidates = [f for f in findings if not CHECKER_TO_DEFECTS.get(f["check"])]

    # 4. GD-18 阴性违规：numeric_consistency 命中阴性行指标名（误报回归）
    negative_violations = [
        f
        for f in findings
        if f["check"] == "numeric_consistency"
        and any(m in f["message"] + f["evidence"] for m in NEGATIVE_LEDGER_MARKERS)
    ]

    per_defect = {
        d: {
            "hit": bool(hit_by_defect[d]),
            "detector": next(x["detector"] for x in manifest["text_injectable"] if x["id"] == d),
            "findings": len(hit_by_defect[d]),
        }
        for d in expected
    }
    tp = sum(1 for v in per_defect.values() if v["hit"])
    return {
        "expected_code_defects": expected,
        "per_defect": per_defect,
        "tp_rate": tp / len(expected) if expected else 1.0,
        "fp_candidates": fp_candidates,
        "negative_violations": negative_violations,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True, help="基线 agent workspace 目录")
    ap.add_argument("--out", required=True)
    ap.add_argument("--manifest", default=str(Path(__file__).parent / "manifest.json"))
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))

    truth = build_regression_ws(Path(args.workspace), manifest, out)
    (out / "checker_ground_truth.json").write_text(
        json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    report = build_report(out / "regression_ws")
    findings = report["findings"]
    (out / "checker_findings.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    result = score(findings, truth, manifest)
    hit = sum(1 for v in result["per_defect"].values() if v["hit"])
    print(f"检查器 finding 总数: {len(findings)}")
    print(
        f"应检出 code 缺陷: {len(result['expected_code_defects'])}，"
        f"命中: {hit}，TP 率: {result['tp_rate']:.0%}"
    )
    print("\n逐缺陷：")
    for d, v in result["per_defect"].items():
        mark = "✅" if v["hit"] else "❌"
        print(f"  {mark} {d} (detector={v['detector']}, findings={v['findings']})")
    print(f"\nGD-18 阴性违规（应=0）: {len(result['negative_violations'])} 条")
    for f in result["negative_violations"]:
        print(f"  {f['file']}:{f['line']} {f['message']}")
    print(f"\nFP 候选（需人工裁决）: {len(result['fp_candidates'])} 条")
    by_checker: dict[str, int] = {}
    for f in result["fp_candidates"]:
        by_checker[f["check"]] = by_checker.get(f["check"], 0) + 1
    for checker, n in sorted(by_checker.items()):
        print(f"  {checker}: {n}")


if __name__ == "__main__":
    main()
