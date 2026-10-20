"""Inject text-injectable golden defects into host slices, with ground truth.

Usage:
    python inject.py --host <report.md> --out <out_dir>

Produces:
    <out_dir>/injected_report.md   host with defects injected at chapter boundaries
    <out_dir>/ground_truth.json    [{defect_id, chapter, span_text}]
"""
import argparse
import json
import re
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--manifest", default=str(Path(__file__).parent / "manifest.json"))
    args = ap.parse_args()

    host = Path(args.host).read_text(encoding="utf-8")
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    parts = re.split(r"(<!-- ===== .+? ===== -->)", host)
    truth = []
    injected_count = 0
    # inject one snippet per defect into successive chapters to spread them out
    chapter_marks = [i for i, p in enumerate(parts) if p.startswith("<!-- =====")]
    for k, d in enumerate(manifest["text_injectable"]):
        snippet = d["snippet"]
        if snippet.startswith("__META__"):
            continue  # meta defects are simulated via workspace state, not text
        if snippet == "__DUP_PARA__":
            snippet = "本段为逐字重复测试段落：双碳政策推动照明行业能效升级，节能改造工程加速落地。（注入用重复段）"
            # inject same paragraph into two chapters
            for j in (0, 1):
                idx = chapter_marks[(k + j) % len(chapter_marks)] + 1
                parts[idx] = parts[idx] + "\n\n" + snippet
            truth.append({"defect_id": d["id"], "occurrences": 2, "span_text": snippet[:40]})
            injected_count += 1
            continue
        idx = chapter_marks[k % len(chapter_marks)] + 1
        parts[idx] = parts[idx] + "\n\n" + snippet
        truth.append({"defect_id": d["id"], "occurrences": 1, "span_text": snippet[:40]})
        injected_count += 1

    (out / "injected_report.md").write_text("".join(parts), encoding="utf-8")
    (out / "ground_truth.json").write_text(
        json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"injected {injected_count} text defects; ground truth -> {out / 'ground_truth.json'}")


if __name__ == "__main__":
    main()
