"""Build only the four public plugin files; does not install or import the worker."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parent
CORE = ROOT.parents[2]
sys.path.insert(0, str(CORE))
from yukino_core.plugin_packages import inspect_package


def build(output):
    files = {}
    for name in ("plugin.json", "worker.py", "README.md", "LICENSE"):
        source = ROOT / name
        if source.is_symlink() or not source.is_file():
            raise ValueError("Expected ordinary source file: " + name)
        payload = source.read_bytes()
        payload.decode("utf-8-sig")
        if source.suffix == ".py":
            compile(payload, name, "exec")
        files[name] = payload
    output.mkdir(parents=True, exist_ok=True)
    archive = output / "hello_counter-1.0.0.zip"
    if archive.exists():
        raise FileExistsError("Use a new output directory: " + str(archive))
    # ZIP root is the plugin root, with deterministic file metadata.
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as package:
        for name, payload in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            package.writestr(info, payload)
    record, _ = inspect_package(archive.read_bytes())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(archive.name + ".sha256").write_text(
        digest + "  " + archive.name + "\n", encoding="ascii"
    )
    return {"id": record["id"], "archive": str(archive), "sha256": digest}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.output), ensure_ascii=False))
