"""重置实验 agent 的长期记忆（T5 记忆隔离）。

用法（Octop 服务停止或目标 agent 空闲时执行）：

    python scripts/reset_agent_memory.py --octop-home <OCTOP_HOME> --agent-id <AgentId>
    python scripts/reset_agent_memory.py --octop-home <OCTOP_HOME> --agent-id <AgentId> --delete

默认把 ``agents/<id>/.octop/memory.sqlite*``（含 -wal/-shm）归档到
``.octop/memory_archive/<时间戳>/``（可取证、可回滚）；``--delete`` 直接删除。
下次启动时 harness 会自动重建空库。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

MEMORY_FILES = ("memory.sqlite", "memory.sqlite-wal", "memory.sqlite-shm")


def archive_memory(octop_home: Path, agent_id: str, *, delete: bool = False) -> list[str]:
    """归档（或删除）agent 的记忆文件，返回处理过的文件名列表。"""
    octop_dir = octop_home / "agents" / agent_id / ".octop"
    if not octop_dir.is_dir():
        raise SystemExit(f"agent 目录不存在：{octop_dir}")

    existing = [octop_dir / name for name in MEMORY_FILES if (octop_dir / name).exists()]
    if not existing:
        print(f"无记忆文件，无需处理：{octop_dir}")
        return []

    wal = octop_dir / "memory.sqlite-wal"
    if wal.exists() and wal.stat().st_size > 0:
        print(
            f"警告：{wal.name} 非空（{wal.stat().st_size} bytes），数据库可能正被使用。"
            "请确认 Octop 服务已停止或该 agent 空闲，否则归档可能带走未落盘数据。",
            file=sys.stderr,
        )

    if delete:
        for f in existing:
            f.unlink()
        print(f"已删除 {len(existing)} 个文件：{[f.name for f in existing]}")
    else:
        dest = octop_dir / "memory_archive" / datetime.now().strftime("%Y%m%d_%H%M%S")
        dest.mkdir(parents=True)
        for f in existing:
            shutil.move(str(f), dest / f.name)
        print(f"已归档 {len(existing)} 个文件 → {dest}")
    return [f.name for f in existing]


def main() -> None:
    ap = argparse.ArgumentParser(description="重置实验 agent 的长期记忆（归档或删除 memory.sqlite）")
    ap.add_argument("--octop-home", required=True, help="OCTOP_HOME 路径（如 .octop-dev）")
    ap.add_argument("--agent-id", required=True, help="实验 agent 的 ID")
    ap.add_argument("--delete", action="store_true", help="直接删除而非归档")
    args = ap.parse_args()
    archive_memory(Path(args.octop_home), args.agent_id, delete=args.delete)


if __name__ == "__main__":
    main()
