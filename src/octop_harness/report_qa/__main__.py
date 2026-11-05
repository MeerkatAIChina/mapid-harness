"""CLI 入口：python -m octop_harness.report_qa <workspace_dir> [--json <out>]。

仅用 stdlib argparse，裸安装即可运行。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from octop_harness.report_qa.report import build_report, format_human


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m octop_harness.report_qa",
        description="Meerkat 报告 workspace 质量检查器（provisional）",
    )
    parser.add_argument("workspace_dir", help="报告 workspace 目录（含 chapters/ 等）")
    parser.add_argument("--json", metavar="OUT", help="将结构化 JSON 结果写入指定文件")
    args = parser.parse_args(argv)

    workspace = Path(args.workspace_dir)
    if not workspace.is_dir():
        print(f"错误：workspace 目录不存在：{workspace}", file=sys.stderr)
        return 2

    result = build_report(workspace)
    if args.json:
        out_path = Path(args.json)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"JSON 结果已写入: {out_path}")
    print(format_human(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
