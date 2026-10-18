"""Run a prepared instance with its private token file, without printing secrets."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="启动 Yukino Core 本地实例")
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18001)
    args = parser.parse_args()
    data = args.data.expanduser().resolve()
    token_file = data / "secrets.json"
    if not token_file.is_file():
        parser.exit(1, "请先执行 python scripts/init_instance.py --data ./data\n")
    tokens = json.loads(token_file.read_text(encoding="utf-8-sig"))
    if not isinstance(tokens.get("admin_token"), str) or len(tokens["admin_token"]) < 16:
        parser.exit(1, "管理令牌无效，请检查私密配置文件。\n")
    os.environ["YUKINO_SECRETS_FILE"] = str(token_file)
    # Running directly from an unpacked source tree also works before pip install.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.argv = ["yukino-core", "--data", str(data), "--resources", str(data / "resources"),
                "--host", args.host, "--port", str(args.port)]
    from yukino_core.__main__ import main as run_core
    run_core()


if __name__ == "__main__":
    main()
