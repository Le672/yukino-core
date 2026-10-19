"""Create an empty local instance. Existing files are never overwritten."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import sys


ROOT = Path(__file__).resolve().parent.parent


def write_new(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def initialize(data: Path, *, docker: bool = False) -> dict:
    data = data.expanduser().resolve()
    data.mkdir(parents=True, exist_ok=True)
    # Refuse partial reuse: especially avoid silently replacing a login token.
    private_files = ("secrets.json", "config.json", "connections.json", "providers.json")
    if any((data / name).exists() for name in private_files) or (data / "resources" / "curated.json").exists():
        raise FileExistsError("目标已存在实例配置；请使用原配置，或选择一个新的空数据目录。")
    config = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8-sig"))
    # The built-in copy bank contains only project-authored public sentences.
    # Existing instances are rejected above and are never silently overwritten.
    if (ROOT / "resources/banter.zh-CN.json").is_file():
        sys.path.insert(0, str(ROOT))
        from yukino_core.banter_bank import load_bank, merge_banter
        config, _ = merge_banter(config, load_bank(ROOT / "resources/banter.zh-CN.json"))
    tokens = {name: secrets.token_urlsafe(32) for name in ("admin_token", "onebot_token", "provider_token")}
    connections = {
        "onebot_url": "ws://host.docker.internal:3001" if docker else "ws://127.0.0.1:3001",
        "self_id": "", "owner_ids": [], "receive_enabled": False,
        "onebot_token": tokens["onebot_token"], "action_timeout": 30,
        "reconnect_min": 1, "reconnect_max": 30,
    }
    write_new(data / "secrets.json", tokens)
    write_new(data / "config.json", config)
    write_new(data / "connections.json", connections)
    write_new(data / "providers.json", {"profiles": [], "default_id": ""})
    resource_root = data / "resources"
    resource_root.mkdir(exist_ok=True)
    (data / "plugins").mkdir(exist_ok=True)
    write_new(resource_root / "curated.json", {"jargons": [], "expressions": [], "knowledge": [], "images": []})
    if os.name != "nt":
        data.chmod(0o700)
    return {"data": str(data), "token_file": str(data / "secrets.json"), "created": True}


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化一个空的 Yukino Core 实例，不覆盖已有文件")
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--docker", action="store_true", help="使用 host.docker.internal 连接宿主机 OneBot")
    args = parser.parse_args()
    try:
        result = initialize(args.data, docker=args.docker)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"初始化失败：{exc}\n")
    print(json.dumps(result, ensure_ascii=False))
    print("管理令牌已写入私密文件的 admin_token 字段；不会输出到终端。首次登录后在控制台配置 QQ 与模型。")


if __name__ == "__main__":
    main()
