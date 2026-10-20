"""report_tools 的单元测试（T2）。

口径与基线案例对齐：
- 合并 = 文件名顺序 + 章节标记 + UTF-8 strict；
- 体量校验用基线真实事故数字（3.45× 膨胀必须被拦）。
"""

from __future__ import annotations

import pytest

from octop_harness import report_tools
from octop_harness.report_tools import (
    DEFAULT_OUTPUT_NAME,
    _check_volume,
    merge_chapter_files,
    stats_for_path,
    text_stats,
)


@pytest.fixture()
def chapters_dir(tmp_path):
    d = tmp_path / "chapters"
    d.mkdir()
    return d


def _write(path, text, *, encoding="utf-8"):
    path.write_text(text, encoding=encoding)


# --- text_stats ---------------------------------------------------------------


def test_text_stats_counts_non_ws_and_cjk():
    stats = text_stats("你好 world\n\n1+1 = 2")
    assert stats["non_ws_chars"] == len("你好world1+1=2")
    assert stats["cjk_chars"] == 2
    assert stats["lines"] == 3


# --- merge_chapter_files ------------------------------------------------------


def test_merge_orders_by_filename_and_marks_chapters(chapters_dir, tmp_path):
    # 乱序创建，验证按文件名排序
    _write(chapters_dir / "03_结论.md", "第三章内容")
    _write(chapters_dir / "01_背景.md", "第一章内容")
    _write(chapters_dir / "02_全景.md", "第二章内容")
    out = tmp_path / DEFAULT_OUTPUT_NAME

    result = merge_chapter_files(chapters_dir, out)

    merged = out.read_text(encoding="utf-8")
    first = merged.index("第一章内容")
    second = merged.index("第二章内容")
    third = merged.index("第三章内容")
    assert first < second < third
    assert "<!-- ===== 01_背景.md ===== -->" in merged
    assert result["files"] == 3
    assert result["total"]["non_ws_chars"] == sum(
        item["non_ws_chars"] for item in result["per_file"]
    ) + sum(
        len(f"<!-- ===== {item['file']} ===== -->".replace(" ", ""))
        for item in result["per_file"]
    )


def test_merge_utf8_cjk_no_bom(chapters_dir, tmp_path):
    _write(chapters_dir / "01_背景.md", "欧普照明竞品分析：智能照明赛道。")
    out = tmp_path / "merged.md"
    merge_chapter_files(chapters_dir, out)
    raw = out.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")  # 无 BOM
    assert "欧普照明" in raw.decode("utf-8")


def test_merge_missing_dir_errors(tmp_path):
    with pytest.raises(ValueError, match="章节目录不存在"):
        merge_chapter_files(tmp_path / "nope", tmp_path / "out.md")


def test_merge_empty_dir_errors(chapters_dir, tmp_path):
    with pytest.raises(ValueError, match="章节目录为空"):
        merge_chapter_files(chapters_dir, tmp_path / "out.md")


def test_merge_invalid_utf8_errors_and_writes_nothing(chapters_dir, tmp_path):
    _write(chapters_dir / "01_背景.md", "正常章节")
    # GBK 编码的中文不是合法 UTF-8
    (chapters_dir / "02_坏.md").write_bytes("坏章节".encode("gbk"))
    out = tmp_path / "out.md"
    with pytest.raises(ValueError, match="不是合法 UTF-8"):
        merge_chapter_files(chapters_dir, out)
    assert not out.exists()


def test_merge_rejects_output_inside_chapters_dir(chapters_dir):
    _write(chapters_dir / "01_背景.md", "内容")
    with pytest.raises(ValueError, match="不能放在章节目录内"):
        merge_chapter_files(chapters_dir, chapters_dir / "merged.md")


# --- 体量校验 ------------------------------------------------------------------


def test_volume_check_blocks_baseline_345x_bloat():
    # 基线事故：合并产物膨胀到章节总和的 3.45×
    with pytest.raises(ValueError, match="体量异常"):
        _check_volume(expected=100_000, actual=345_000)


def test_volume_check_allows_within_tolerance():
    _check_volume(expected=100_000, actual=104_999)
    _check_volume(expected=100_000, actual=95_001)


def test_merge_volume_self_check_passes(chapters_dir, tmp_path):
    _write(chapters_dir / "01.md", "内容" * 100)
    out = tmp_path / "out.md"
    merge_chapter_files(chapters_dir, out)  # 不抛异常即通过
    assert out.is_file()


# --- stats_for_path -----------------------------------------------------------


def test_stats_for_single_file(tmp_path):
    p = tmp_path / "a.md"
    _write(p, "你好 world")
    result = stats_for_path(p)
    assert result["files"] == 1
    assert result["total"]["non_ws_chars"] == len("你好world")
    assert result["total"]["cjk_chars"] == 2


def test_stats_for_dir_aggregates(chapters_dir):
    _write(chapters_dir / "01.md", "一二三四五")
    _write(chapters_dir / "02.md", "abc def")
    result = stats_for_path(chapters_dir)
    assert result["files"] == 2
    assert result["total"]["cjk_chars"] == 5
    assert result["total"]["non_ws_chars"] == 5 + len("abcdef")
    assert [item["file"] for item in result["per_file"]] == ["01.md", "02.md"]


def test_stats_missing_path_errors(tmp_path):
    with pytest.raises(ValueError, match="路径不存在"):
        stats_for_path(tmp_path / "nope.md")


# --- 插件层（无运行时 config 上下文） -------------------------------------------


class _FakeCtx:
    def __init__(self):
        self.tools = {}

    def tool(self, name, fn, *, description=""):
        self.tools[name] = fn


@pytest.fixture()
def plugin_tools():
    ctx = _FakeCtx()
    report_tools.register(ctx)
    return ctx.tools


def test_register_exposes_both_tools(plugin_tools):
    assert set(plugin_tools) == {"merge_chapters", "report_stats"}


def test_plugin_merge_with_absolute_paths(plugin_tools, chapters_dir, tmp_path):
    _write(chapters_dir / "01.md", "章节内容")
    out = tmp_path / "merged.md"
    text = plugin_tools["merge_chapters"](str(chapters_dir), str(out))
    assert text.startswith("合并完成。")
    assert "非空白字符" in text
    assert out.is_file()


def test_plugin_relative_path_without_root_returns_error(plugin_tools):
    text = plugin_tools["merge_chapters"]("chapters", "out.md")
    assert text.startswith("Error: ")
    assert "workspace_root" in text


def test_plugin_report_stats_with_absolute_path(plugin_tools, tmp_path):
    p = tmp_path / "a.md"
    _write(p, "你好世界")
    text = plugin_tools["report_stats"](str(p))
    assert "汉字 4" in text


def test_plugin_merge_error_returns_string_not_raise(plugin_tools, tmp_path):
    text = plugin_tools["merge_chapters"](str(tmp_path / "nope"), str(tmp_path / "o.md"))
    assert text.startswith("Error: ")
