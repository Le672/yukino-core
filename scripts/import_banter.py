"""Import after the operator stops the instance; Windows marker is extra protection.

This script cannot reliably detect running Linux/Docker instances and does not
hold a shared process lock. Stop the service and other config writers first.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from yukino_core.banter_bank import load_bank, merge_banter


def import_copy(data: Path, *, include_keywords=False, dry_run=False) -> dict:
    """The caller must stop the core and other writers before invoking this."""
    data = data.resolve()
    config = data / "config.json"
    if config.is_symlink() or not config.is_file():
        raise ValueError("请提供已有实例的普通config.json所在目录。")
    if (data / "runtime.json").exists():
        raise ValueError("存在 Windows 启动器标记；请先正常停止实例并核实标记后再导入。")
    old = config.read_bytes()
    result, summary = merge_banter(json.loads(old.decode("utf-8-sig")), load_bank(), include_keywords=include_keywords)
    summary["dry_run"] = dry_run
    if dry_run:
        return summary
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup = data / "backups" / ("banter-" + stamp)
    if any(parent.is_symlink() for parent in (backup, backup.parent)):
        raise ValueError("拒绝链接备份目录。")
    backup.mkdir(parents=True)
    shutil.copy2(config, backup / "config.json")
    stat = config.stat()
    temporary = data / (".banter-" + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(stat.st_mode & 0o777)
        if hasattr(os, "chown"):
            os.chown(temporary, stat.st_uid, stat.st_gid)
        # Catch edits observed before replacement; this is not a process lock.
        # Keep the core, control panel and other configuration writers stopped.
        if config.read_bytes() != old:
            raise ValueError("配置在导入期间改变，未覆盖。")
        os.replace(temporary, config)
    finally:
        temporary.unlink(missing_ok=True)
    summary["backup"] = str(backup / "config.json")
    return summary


def main():
    parser = argparse.ArgumentParser(
        description="停机后追加原创戳一戳/搭话文案；备份且保留自定义内容",
        epilog="导入前必须先停止核心服务（包括 Linux/Docker）和其他配置写入程序。runtime.json 仅是 Windows 启动器的额外保护；缺少它不表示实例已停止，脚本不能可靠检测所有运行中的实例。",
    )
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--include-keywords", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        summary = import_copy(args.data, include_keywords=args.include_keywords, dry_run=args.dry_run)
    except (OSError, ValueError) as exc:
        parser.exit(1, str(exc) + "\n")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
