"""Build a deterministic, allowlisted source release, never a VPS backup.

Only core Python, text UI, tests, release tools and documentation are included,
along with the named Windows tooling, example and five text-only plugin packs.
Runtime settings, user resources, optional deployment integrations and the private
deployment logo are excluded. Text UI references are changed only in the archive.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parent.parent
ROOT_FILES = (
    "pyproject.toml", "requirements.txt", "Dockerfile", "LICENSE", "README.md", "ARCHITECTURE.md",
    "THIRD_PARTY_NOTICES.md", "CHANGELOG.md", "config.example.json",
    ".env.example", "compose.release.yaml",
)
RELEASE_SCRIPTS = ("init_instance.py", "run.py", "build_release.py")
SOURCE_DIRECTORIES = (
    "windows", "examples/plugins/hello_counter", "extensions/community_pack",
    "extensions/maibot_pack", "extensions/expansion_pack", "extensions/market2_pack",
    "extensions/interaction_pack", ".github/workflows",
)
TEXT_SUFFIXES = {".py", ".json", ".md", ".txt", ".yml", ".yaml"}
FORBIDDEN_PARTS = {"__pycache__", ".git", ".venv", "node_modules", "data", "credentials", "backups", "evidence", "dist", "build"}
LOGO = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">'
        '<rect width="128" height="128" rx="30" fill="#233d37"/>'
        '<text x="64" y="89" text-anchor="middle" font-family="sans-serif" '
        'font-size="88" font-weight="700" fill="#ffffff">Y</text></svg>\n').encode()


def contains_private_data(decoded: str) -> bool:
    # Publisher-specific forbidden values are supplied privately, never stored in
    # the release script or copied to the manifest. Use one value per line.
    markers = [value.strip() for value in os.environ.get("YUKINO_RELEASE_FORBIDDEN", "").splitlines() if value.strip()]
    if any(marker in decoded for marker in markers):
        return True
    if re.search(r"-----BEGIN (?:OPENSSH |RSA |EC |DSA |ENCRYPTED )?PRIVATE KEY-----", decoded):
        return True
    if re.search(r"\bsk-[A-Za-z0-9_-]{24,}", decoded):
        return True
    if re.search(r'''["']?(?:self_id|owner_id|owner_ids|YUKINO_SELF_ID|YUKINO_OWNER_ID)["']?\s*[:=]\s*(?:["']|\[\s*["']?)?[0-9]{5,}''', decoded):
        return True
    # Explicitly allow loopback/bind addresses, private-network examples and the
    # RFC 5737 documentation networks. A fixed public server IP needs review.
    examples = [ipaddress.ip_network(value) for value in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")]
    for candidate in re.findall(r"(?<![\w.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![\w.])", decoded):
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        # Public DNS endpoints and non-routable network-test fixtures are
        # intentional source content, not a publisher's deployment address.
        if not (address.is_loopback or address.is_unspecified or address.is_private
                or address.is_multicast or address in ipaddress.ip_network("100.64.0.0/10")
                or str(address) in {"1.1.1.1", "8.8.8.8"}
                or any(address in network for network in examples)):
            return True
    return False


def accepted_path(root: Path, path: Path) -> bool:
    relative = path.relative_to(root)
    return (not any(part in FORBIDDEN_PARTS for part in relative.parts)
            and not any(parent.is_symlink() for parent in (path, *path.parents) if parent != root.parent)
            and path.resolve().is_relative_to(root.resolve()) and path.is_file())


def add_file(files: dict[str, bytes], root: Path, path: Path) -> None:
    if not accepted_path(root, path):
        raise ValueError("拒绝非普通源文件或越界路径：" + str(path.relative_to(root)))
    relative = path.relative_to(root).as_posix()
    if path.stat().st_size > 2_000_000:
        raise ValueError("发行源文件超过 2 MB 上限：" + relative)
    payload = path.read_bytes()
    decoded = payload.decode("utf-8-sig")
    if contains_private_data(decoded):
        raise ValueError("发现私人部署标记，请先移除再发行：" + relative)
    if relative.startswith("yukino_core/static/"):
        decoded = decoded.replace("brand-logo.png", "brand-logo.svg").replace('type="image/png"', 'type="image/svg+xml"')
        payload = decoded.encode("utf-8")
    files[relative] = payload


def collect_files(root: Path) -> tuple[dict[str, bytes], str]:
    root = root.resolve()
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8-sig"))
    version = metadata["project"]["version"]
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[a-z0-9.-]+)?", version):
        raise ValueError("项目版本号无效")
    files: dict[str, bytes] = {}
    for name in ROOT_FILES:
        add_file(files, root, root / name)
    for path in sorted((root / "yukino_core").rglob("*.py")):
        if "__pycache__" not in path.parts:
            add_file(files, root, path)
    for path in sorted((root / "yukino_core" / "static").iterdir()):
        if path.suffix in (".html", ".css", ".js"):
            add_file(files, root, path)
    for path in sorted((root / "tests").glob("*.py")):
        add_file(files, root, path)
    for path in sorted((root / "docs").rglob("*")):
        if path.is_file() and path.suffix in (".md", ".txt", ".json"):
            add_file(files, root, path)
    for name in RELEASE_SCRIPTS:
        add_file(files, root, root / "scripts" / name)
    for directory in SOURCE_DIRECTORIES:
        for path in sorted((root / directory).rglob("*")):
            relative = path.relative_to(root / directory)
            if (not path.is_file() or any(part in FORBIDDEN_PARTS for part in relative.parts)
                    or not (path.suffix in TEXT_SUFFIXES or path.name in {"LICENSE", "NOTICE", "COPYING"})):
                continue
            add_file(files, root, path)
    files["yukino_core/static/brand-logo.svg"] = LOGO
    files["resources/curated.json"] = b'{"jargons":[],"expressions":[],"knowledge":[],"images":[]}\n'
    # The source-tree Docker ignore contains no account material.
    files[".dockerignore"] = b".git\n.venv\n__pycache__\n*.pyc\ndata\ndist\ntests\n.env\n*.db\n"
    files[".gitignore"] = (b".venv/\n__pycache__/\n*.pyc\ndata/\ndist/\n.env\n*.db\n*.db-*\n*.egg-info/\n"
                           b"build/\nwindows-cache/\nreleases/\n*.pem\n*.key\n.DS_Store\n")
    return files, version


def build(root: Path, output: Path) -> dict:
    files, version = collect_files(root)
    prefix = "yukino-core-" + version
    entries = [{"path": name, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
               for name, payload in sorted(files.items())]
    manifest = {
        "format": 1, "project": "yukino-core", "version": version, "artifact": "source",
        "entries": entries, "contains_credentials": False, "contains_user_resources": False,
        "logo": "generated Y letter SVG; private deployment image excluded",
        "optional_integrations_included": False,
        "windows_build_tools_included": True,
        "external_plugin_source_packs": ["community_pack", "maibot_pack", "expansion_pack", "market2_pack", "interaction_pack"],
        "licensing_status": "review_required_see_docs_LICENSE_REVIEW",
        "omitted": ["runtime data and tokens", "user persona and histories", "QQ accounts",
                    "meme image packs", "private logo", "MaiBot databases", "deployment integrations"],
    }
    files["release-manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    archive = output / (prefix + "-source.zip")
    if archive.exists():
        raise FileExistsError("发行包已存在；请使用新的输出目录：" + str(archive))
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as package:
        for name, payload in sorted(files.items()):
            info = zipfile.ZipInfo(prefix + "/" + name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            package.writestr(info, payload)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / (archive.name + ".sha256")).write_text(digest + "  " + archive.name + "\n", encoding="ascii")
    manifest_path = output / (prefix + "-release-manifest.json")
    manifest_path.write_text(json.dumps({**manifest, "archive": archive.name, "sha256": digest},
                                        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"archive": str(archive), "sha256": digest, "manifest": str(manifest_path), "files": len(files)}


def main() -> None:
    parser = argparse.ArgumentParser(description="构建不包含私人部署资料的白名单源码发行包")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    try:
        result = build(ROOT, args.output)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"发行构建失败：{exc}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
