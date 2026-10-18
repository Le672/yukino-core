import argparse
import os
from pathlib import Path
from aiohttp import web
from .app import make_runtime
from .config import Config


def main():
    parser = argparse.ArgumentParser(description="Yukino Core QQ kernel")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18001)
    parser.add_argument("--data", default=os.environ.get("YUKINO_DATA", "./data"))
    parser.add_argument("--resources", default=str(Path(__file__).parent.parent / "resources"))
    parser.add_argument("--legacy", default=os.environ.get("YUKINO_LEGACY_ROOT"))
    args = parser.parse_args()
    config = Config(Path(args.data) / "config.json")
    web.run_app(make_runtime(config, args.data, args.resources, args.legacy), host=args.host, port=args.port, print=None)


if __name__ == "__main__": main()
