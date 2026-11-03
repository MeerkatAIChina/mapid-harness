"""同源三格式构建（设计文档_R4 §6.1）：同一份 md → docx/html/pdf。

移植自同事管线 build_outputs_professional.py，改为函数 + CLI 双形态，
去掉硬编码报告名与 sys.path 黑魔法。
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from octop_harness.pipeline.report_renderer import convert

_MIN_BYTES = 10_000


def build_outputs(md_path: Path, out_dir: Path, base_name: str) -> dict[str, dict]:
    """从同一份 md 生成三格式；任一格式异常小即 RuntimeError。"""
    md = Path(md_path).read_text(encoding="utf-8")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict] = {}
    for fmt in ("docx", "html", "pdf"):
        out = out_dir / f"{base_name}.{fmt}"
        t0 = time.time()
        convert(fmt, md, str(out))
        size = out.stat().st_size
        if size < _MIN_BYTES:
            raise RuntimeError(f"{fmt} 输出异常小: {size}")
        results[fmt] = {"bytes": size, "seconds": round(time.time() - t0, 1)}
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="同源三格式构建")
    parser.add_argument("md_path", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--base-name", default="report")
    args = parser.parse_args()
    results = build_outputs(args.md_path, args.out_dir, args.base_name)
    for fmt, info in results.items():
        print(fmt, info, flush=True)


if __name__ == "__main__":
    main()
