"""内建报告工具：``merge_chapters`` / ``report_stats``（Meerkat 长报告工作流）。

Provisional：内部模块，API 可能变，不进公共 ``__all__``。

运行时通过 Octop 插件机制挂载：插件 shim（``.octop-dev/plugins/meerkat-report/``）
的 ``setup(ctx)`` 调用 :func:`register`。工具内不依赖 harness 内核对象，
workspace 根目录经 agent ``config_json`` 的插件配置注入（``workspace_root``），
两个 repo 的内核均零改动。

字数口径与 :mod:`octop_harness.report_qa` 一致：非空白字符数 + 汉字数。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from octop_harness.plugins.context import PluginContext

# --- Meerkat 约定（集中一处，与 report_qa.constants 对齐） --------------------
CHAPTERS_DIR = "chapters"
DEFAULT_OUTPUT_NAME = "report_merged.md"
CHAPTER_MARKER = "<!-- ===== {name} ===== -->"
VOLUME_TOLERANCE = 0.05  # 合并产物与章节总和的体量偏差阈值（基线 3.45× 膨胀护栏）
DELIVERY_DIR_NAME = "rendered"  # 交付产物目录（workspace 下，与 round-4 产物约定一致）


# --- 统计口径 ------------------------------------------------------------------
def text_stats(text: str) -> dict[str, int]:
    """字数统计：非空白字符数 / 汉字数 / 行数。"""
    return {
        "non_ws_chars": len(re.sub(r"\s+", "", text)),
        "cjk_chars": len(re.findall(r"[一-鿿]", text)),
        "lines": text.count("\n") + 1 if text else 0,
    }


# --- 合并 ----------------------------------------------------------------------
def _check_volume(expected: int, actual: int) -> None:
    """体量自校验：合并产物非空白字符数 ≈ 章节总和 ±VOLUME_TOLERANCE。"""
    if expected <= 0:
        return
    deviation = abs(actual - expected) / expected
    if deviation > VOLUME_TOLERANCE:
        raise ValueError(
            f"合并体量异常：章节总和 {expected:,} 非空白字符，合并产物 {actual:,}，"
            f"偏差 {deviation:.1%} 超过 ±{VOLUME_TOLERANCE:.0%}。"
            "合并已中止，请检查章节文件编码（必须 UTF-8）后重试。"
        )


def merge_chapter_files(chapters_dir: Path, output: Path) -> dict[str, Any]:
    """按文件名顺序合并 ``chapters_dir/*.md`` 到 ``output``。

    - 全程 UTF-8 strict 读写（读不出就直接报错，绝不静默产出乱码）；
    - 每章前插入 ``CHAPTER_MARKER`` 分隔标记（供检查器归因到章节）；
    - 合并后做体量自校验，异常则抛错且不写产出文件。

    返回摘要 dict（files / 每章统计 / 合计 / output）。
    """
    if not chapters_dir.is_dir():
        raise ValueError(f"章节目录不存在：{chapters_dir}")
    chapters = sorted(chapters_dir.glob("*.md"))
    if not chapters:
        raise ValueError(f"章节目录为空（无 .md 文件）：{chapters_dir}")
    output = output.resolve()
    if output.parent == chapters_dir.resolve():
        raise ValueError(f"输出文件不能放在章节目录内（会被后续合并再次收入）：{output}")

    parts: list[str] = []
    per_file: list[dict[str, Any]] = []
    for path in chapters:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"章节文件不是合法 UTF-8：{path.name}（{exc}）。请修正编码后重新合并。") from exc
        per_file.append({"file": path.name, **text_stats(text)})
        parts.append(f"{CHAPTER_MARKER.format(name=path.name)}\n\n{text}")

    merged = "\n\n".join(parts) + "\n"
    total = text_stats(merged)
    # 校验基线 = 章节总和 + 标记自身开销（分隔符全是空白字符，不计入）。
    # 正常情况下偏差恒为 0，容差只吸收真正的异常（编码事故、内容丢失）。
    marker_overhead = sum(len(re.sub(r"\s+", "", CHAPTER_MARKER.format(name=path.name))) for path in chapters)
    _check_volume(
        sum(item["non_ws_chars"] for item in per_file) + marker_overhead,
        total["non_ws_chars"],
    )

    output.write_text(merged, encoding="utf-8")  # 无 BOM
    return {
        "output": str(output),
        "files": len(per_file),
        "per_file": per_file,
        "total": total,
    }


# --- 交付链（设计 §5.1/§6.1：确定性工序，不进模型职责） --------------------------
def _clean_chapters(chapters_dir: Path) -> int:
    """逐章清理生成残留（merge 前自动跑），返回删除的残留行数。

    只删 ``clean_report_markup.FORBIDDEN_PATTERNS`` 命中行（围栏内容不动）；
    文件无命中不改写。UTF-8 strict，坏编码直接抛 ``UnicodeDecodeError``。
    """
    from octop_harness.pipeline.clean_report_markup import clean_text

    removed = 0
    for path in sorted(chapters_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        cleaned, stats = clean_text(text)
        if stats["removed"]:
            path.write_text(cleaned, encoding="utf-8")
            removed += stats["removed"]
    return removed


def _run_delivery_chain(merged_path: Path) -> list[str]:
    """同源三格式构建 → 交付验收（设计 §6.1 收尾工序）。

    产物落在 ``<merged 同级>/rendered/``，基名取合并文件 stem。返回失败清单
    （空 = 通过）；依赖缺失、构建异常一律 fail-closed 记为失败——未检查 ≠ 通过。
    """
    try:
        from octop_harness.pipeline.build_outputs import build_outputs
        from octop_harness.pipeline.validate_report import validate_report
    except ImportError as exc:
        return [f"交付链依赖缺失（pipeline extras 未安装）：{exc}"]
    merged_path = Path(merged_path)
    out_dir = merged_path.parent / DELIVERY_DIR_NAME
    base_name = merged_path.stem
    try:
        build_outputs(merged_path, out_dir, base_name)
    except Exception as exc:  # 图表契约 ValueError / 体量 RuntimeError / IO 异常，全是交付失败
        return [f"三格式构建失败：{exc}"]
    return validate_report(out_dir, base_name)


def stats_for_path(path: Path) -> dict[str, Any]:
    """统计单个 .md 文件或目录（顶层 *.md）的字数。"""
    if path.is_dir():
        files = sorted(path.glob("*.md"))
        if not files:
            raise ValueError(f"目录中无 .md 文件：{path}")
        per_file: list[dict[str, Any]] = [{"file": f.name, **text_stats(f.read_text(encoding="utf-8"))} for f in files]
        total = {key: sum(item[key] for item in per_file) for key in ("non_ws_chars", "cjk_chars", "lines")}
        return {"path": str(path), "files": len(per_file), "per_file": per_file, "total": total}
    if not path.is_file():
        raise ValueError(f"路径不存在：{path}")
    return {"path": str(path), "files": 1, "total": text_stats(path.read_text(encoding="utf-8"))}


# --- 插件挂载 ------------------------------------------------------------------
def _format_stats(result: dict[str, Any]) -> str:
    total = result["total"]
    lines = [
        f"路径: {result['path']}",
        f"文件数: {result['files']}",
        f"合计: 非空白字符 {total['non_ws_chars']:,}，汉字 {total['cjk_chars']:,}，行 {total['lines']:,}",
    ]
    for item in result.get("per_file", []):
        lines.append(f"  {item['file']}: 非空白字符 {item['non_ws_chars']:,}，汉字 {item['cjk_chars']:,}")
    return "\n".join(lines)


def _workspace_root(tool_name: str) -> Path | None:
    """从 agent 插件配置取 workspace 根目录（无运行时上下文时为 ``None``）。"""
    try:
        from octop_harness.plugins.tools import get_tool_config
    except ImportError:
        return None
    try:
        cfg = get_tool_config(tool_name)
    except RuntimeError:
        return None
    raw = (cfg or {}).get("workspace_root")
    return Path(raw) if isinstance(raw, str) and raw.strip() else None


_NO_ROOT_ERROR = (
    "Error: 相对路径需要配置 workspace_root（agent config_json → plugins → "
    "meerkat-report → tools → 本工具 → config.workspace_root），或直接传绝对路径。"
)


def register(ctx: PluginContext) -> None:
    """插件入口：注册 ``merge_chapters`` 与 ``report_stats``。"""

    def _resolve(tool_name: str, raw: str) -> Path | str:
        path = Path(raw)
        if path.is_absolute():
            return path
        root = _workspace_root(tool_name)
        if root is None:
            return _NO_ROOT_ERROR
        return root / path

    def merge_chapters(
        chapters_dir: str = CHAPTERS_DIR,
        output: str = DEFAULT_OUTPUT_NAME,
    ) -> str:
        """把 chapters 目录里的章节 .md 按文件名顺序合并成一份完整报告（UTF-8）。

        每章前会插入章节标记；合并后自动做体量校验（合并产物非空白字符数 ≈
        章节总和 ±5%，异常即报错且不写文件）。禁止自写脚本合并，必须用这个工具。
        合并前后自动执行交付链（merge 前清理生成残留 → 三格式构建 → 交付验收），
        任一环未通过即返回 Error 与失败清单，修复后重新调用本工具即可。

        Args:
            chapters_dir: 章节目录（相对 workspace 根目录，默认 "chapters"）。
            output: 合并产出文件（相对 workspace 根目录，默认 "report_merged.md"）。
        """
        src = _resolve("merge_chapters", chapters_dir)
        if isinstance(src, str):
            return src
        dst = _resolve("merge_chapters", output)
        if isinstance(dst, str):
            return dst
        try:
            removed = _clean_chapters(src)
            result = merge_chapter_files(src, dst)
        except ValueError as exc:
            return f"Error: {exc}"
        except UnicodeDecodeError as exc:
            return f"Error: 章节文件不是合法 UTF-8（{exc}）"
        summary = _format_stats(
            {
                "path": result["output"],
                "files": result["files"],
                "per_file": result["per_file"],
                "total": result["total"],
            }
        )
        failures = _run_delivery_chain(dst)
        if failures:
            detail = "\n".join(f"- {f}" for f in failures)
            return f"Error: 交付链未通过（合并本体已完成，修复下列问题后重新调用本工具即可）：\n{detail}"
        return f"合并完成。\n{summary}\n交付链：清理残留 {removed} 行；三格式构建与交付验收通过。"

    def report_stats(path: str = CHAPTERS_DIR) -> str:
        """统计报告字数（口径：非空白字符数 + 汉字数）。

        禁止自报字数，所有字数一律以本工具输出为准；把输出原样写入 progress.md。

        Args:
            path: 要统计的 .md 文件或目录（目录统计顶层 *.md；相对 workspace
                根目录，默认 "chapters"）。
        """
        target = _resolve("report_stats", path)
        if isinstance(target, str):
            return target
        try:
            return _format_stats(stats_for_path(target))
        except ValueError as exc:
            return f"Error: {exc}"
        except UnicodeDecodeError as exc:
            return f"Error: 文件不是合法 UTF-8（{exc}）"

    ctx.tool(
        "merge_chapters",
        merge_chapters,
        description="把 chapters 目录的章节 .md 按文件名顺序合并为完整报告（UTF-8 + 体量自校验 ±5%）",
    )
    ctx.tool(
        "report_stats",
        report_stats,
        description="统计报告字数（非空白字符数 + 汉字数），字数一律以本工具输出为准",
    )
