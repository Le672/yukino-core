"""Bounded local retrieval over curated Chinese resources; no model requests.

Only explicit resource manifests are read. A mounted MaiBot database is opened
read-only and only its image registry is considered; descriptions must match the
bundled curated catalogue. Chat, user, memory and expression tables are never
queried. Merely marking a legacy record 'global' does not make it public data.
"""

from __future__ import annotations

import asyncio
import copy
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import random
import re
import sqlite3
import uuid


MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_ROWS = 10_000
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MAX_IMAGE_BYTES = 5_000_000
KINDS = ("images", "jargon", "expressions", "knowledge")
PUBLIC_FIELDS = {"images": ("description", "tags"), "jargon": ("text", "meaning"),
                 "expressions": ("situation", "style", "examples"), "knowledge": ("title", "text")}


def _text(value, maximum: int = 1000) -> str:
    return value.strip()[:maximum] if isinstance(value, str) else ""


def _tags(value) -> list[str]:
    values = value if isinstance(value, list) else [value]
    result = []
    for item in values[:100]:
        if isinstance(item, str):
            result.extend(part.strip()[:60] for part in re.split(r"[,，、;；\s]+", item) if part.strip())
    return list(dict.fromkeys(result))[:40]


def _tokens(query: str) -> set[str]:
    terms = set()
    for part in re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]+", query[:1000].lower()):
        terms.add(part[:60])
        if re.search(r"[\u3400-\u9fff]", part):
            terms.update(part[i:i + 2] for i in range(min(len(part) - 1, 80)))
    return set(sorted(terms, key=lambda item: (-len(item), item))[:96])


def _score(query: str, tokens: set[str], primary: str, secondary: str = "") -> int:
    primary, secondary = primary.lower(), secondary.lower()
    score = 0
    if primary and primary in query:
        score += 100 + min(len(primary), 30)
    for token in tokens:
        if token in primary:
            score += 5 + min(len(token), 8)
        elif token in secondary:
            score += 1
    return score


class ResourceLibrary:
    def __init__(self, root: str | Path, legacy_root: str | Path | None = None):
        self.root = Path(root).resolve()
        self.legacy_root = Path(legacy_root).resolve() if legacy_root else None
        self._jargon: list[dict] = []
        self._knowledge: list[dict] = []
        self._expressions: list[dict] = []
        self._images: list[dict] = []
        self._catalogue: dict[str, dict] = {}
        self._errors: list[str] = []
        self._sources: list[str] = []
        self._rejected = 0
        self._legacy_state = "not_configured" if not legacy_root else "not_found"
        self._legacy_images = 0
        self._opened = False
        self._lock = asyncio.Lock()
        self._base_records = {kind: {} for kind in KINDS}
        self._records = {kind: {} for kind in KINDS}
        self._overrides = {kind: {} for kind in KINDS}

    async def open(self) -> None:
        # Parse files, inspect SQLite and verify media off the event loop.
        async with self._lock:
            await self._offload(self._load)

    @staticmethod
    async def _offload(operation, *args):
        # Cancellation must not release the catalogue lock while a worker is
        # still committing a file and updating the in-memory snapshot.
        worker = asyncio.create_task(asyncio.to_thread(operation, *args))
        cancelled = False
        while True:
            try:
                result = await asyncio.shield(worker)
                break
            except asyncio.CancelledError:
                if worker.cancelled():
                    raise
                cancelled = True
            except BaseException:
                if cancelled:
                    raise asyncio.CancelledError() from None
                raise
        if cancelled:
            raise asyncio.CancelledError()
        return result

    def _load(self) -> None:
        self._jargon, self._knowledge, self._expressions, self._images = [], [], [], []
        self._catalogue, self._errors, self._sources = {}, [], []
        self._rejected, self._legacy_images = 0, 0
        for filename in ("curated.json", "jargons.json", "knowledge.json", "expressions.json", "images.json"):
            path = self.root / filename
            if not path.exists():
                continue
            try:
                if not path.resolve().is_relative_to(self.root) or path.stat().st_size > MAX_JSON_BYTES:
                    raise ValueError("resource file outside root or too large")
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
                if isinstance(payload, list) and filename != "curated.json":
                    payload = {filename[:-5]: payload}
                if not isinstance(payload, dict):
                    raise ValueError("resource manifest must be an object")
                self._read_manifest(payload)
                self._sources.append(filename)
            except (OSError, ValueError, TypeError, RecursionError):
                self._errors.append(f"{filename}: invalid or unreadable resource file")
        self._dedupe_text()
        if self.legacy_root is not None:
            self._load_legacy_images()
        seen_paths, seen_hashes, unique_images = set(), set(), []
        for item in self._images:
            digest = item["sha256"].lower()
            if item["path"] in seen_paths or (digest and digest in seen_hashes):
                continue
            seen_paths.add(item["path"])
            if digest:
                seen_hashes.add(digest)
            unique_images.append(item)
        self._images = unique_images
        self._index_base()
        self._load_overlay()
        self._apply_overlay()
        self._opened = True

    def _rows(self, payload: dict, key: str) -> list:
        value = payload.get(key, [])
        if not isinstance(value, list):
            self._rejected += 1
            return []
        if len(value) > MAX_ROWS:
            self._rejected += len(value) - MAX_ROWS
        return value[:MAX_ROWS]

    def _read_manifest(self, payload: dict) -> None:
        for row in self._rows(payload, "jargons") + self._rows(payload, "jargon"):
            if not isinstance(row, dict):
                self._rejected += 1
                continue
            text = _text(row.get("text") or row.get("content"), 100)
            meaning = _text(row.get("meaning"), 600)
            if not text or not meaning:
                self._rejected += 1
                continue
            self._jargon.append({"text": text, "content": text, "meaning": meaning})
        for row in self._rows(payload, "knowledge"):
            if not isinstance(row, (str, dict)):
                self._rejected += 1
                continue
            text = _text(row if isinstance(row, str) else row.get("text") or row.get("content"), 1200)
            if not text:
                self._rejected += 1
                continue
            title = _text(row.get("title"), 120) if isinstance(row, dict) else ""
            self._knowledge.append({"text": text, "content": text, "title": title, "meaning": text})
        for row in self._rows(payload, "expressions"):
            if not isinstance(row, dict):
                self._rejected += 1
                continue
            situation, style = _text(row.get("situation"), 200), _text(row.get("style"), 300)
            raw = row.get("examples", row.get("content_list", []))
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except (ValueError, RecursionError):
                    raw = []
            examples = [_text(item, 200) for item in raw[:4] if isinstance(item, str)] if isinstance(raw, list) else []
            if not situation or not style:
                self._rejected += 1
                continue
            self._expressions.append({"situation": situation, "style": style, "examples": examples,
                                      "text": situation + "：" + style, "meaning": style})
        for row in self._rows(payload, "images") + self._rows(payload, "media_catalogue"):
            if not isinstance(row, dict):
                self._rejected += 1
                continue
            description = _text(row.get("description"), 500)
            if not description:
                self._rejected += 1
                continue
            item = {"id": _text(row.get("id") or row.get("key") or row.get("sha256"), 100)
                    or hashlib.sha256(description.encode()).hexdigest()[:20],
                    "description": description,
                    "tags": _tags(row.get("tags", row.get("emotion", ""))) + _tags(row.get("category", "")),
                    "sha256": _text(row.get("sha256"), 64)}
            self._catalogue.setdefault(description, item)
            raw_path = row.get("path") or row.get("local_path") or row.get("file")
            if raw_path:
                resolved = self._image_path(self.root, raw_path)
                accepted = self._accepted_image(resolved, item, "bundled") if resolved else None
                if accepted:
                    self._images.append(accepted)
                else:
                    self._rejected += 1

    def _dedupe_text(self) -> None:
        for attr, key in (("_jargon", "text"), ("_knowledge", "text"), ("_expressions", "situation")):
            seen = set()
            setattr(self, attr, [row for row in getattr(self, attr)
                                if not (row[key] in seen or seen.add(row[key]))])

    @staticmethod
    def _verify_hash(path: Path, expected: str) -> bool:
        if not expected:
            return True
        if not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
            return False
        try:
            with path.open("rb") as stream:
                return hashlib.file_digest(stream, "sha256").hexdigest() == expected.lower()
        except OSError:
            return False

    @staticmethod
    def _accepted_image(path: Path, item: dict, source: str) -> dict | None:
        expected = item.get("sha256", "")
        if expected and not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
            return None
        try:
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if expected and digest != expected.lower():
                return None
            return {**item, "id": "img_" + digest[:24], "sha256": digest,
                    "path": str(path), "source": source}
        except OSError:
            return None

    @staticmethod
    def _image_path(root: Path, raw) -> Path | None:
        if not isinstance(raw, str) or not raw or "\x00" in raw or "://" in raw:
            return None
        # Reject traversal under either platform's syntax before resolving.
        normalized = raw.replace("\\", "/")
        if ".." in PurePosixPath(normalized).parts:
            return None
        candidate = Path(raw)
        if PureWindowsPath(raw).drive and not candidate.is_absolute():
            return None
        if not candidate.is_absolute():
            candidate = root / normalized
        try:
            candidate = candidate.resolve(strict=True)
            if (not candidate.is_relative_to(root) or not candidate.is_file()
                    or candidate.suffix.lower() not in IMAGE_SUFFIXES
                    or not 0 < candidate.stat().st_size <= MAX_IMAGE_BYTES):
                return None
        except (OSError, ValueError, RuntimeError):
            return None
        return candidate

    def _legacy_path(self, raw) -> Path | None:
        if not isinstance(raw, str) or not self.legacy_root:
            return None
        normalized = raw.replace("\\", "/")
        if ".." in PurePosixPath(normalized).parts or "://" in normalized:
            return None
        media_root = (self.legacy_root / "data" / "emoji").resolve()
        if not media_root.is_relative_to(self.legacy_root):
            return None
        path = Path(raw)
        if path.is_absolute() and path.is_relative_to(self.legacy_root):
            return self._image_path(media_root, str(path))
        # MaiBot stores paths relative to its project root, and older records
        # may contain the original deployment path before the read-only mount.
        for prefix in ("/opt/yukino-bot/maibot/data/emoji/", "data/emoji/", "emoji/"):
            if normalized.startswith(prefix):
                return self._image_path(media_root, normalized[len(prefix):])
        if "/" not in normalized and not PureWindowsPath(raw).drive:
            return self._image_path(media_root, normalized)
        return None

    def _load_legacy_images(self) -> None:
        db_path = self.legacy_root / "data" / "MaiBot.db"
        if not db_path.is_file():
            self._legacy_state = "not_found"
            return
        try:
            if not db_path.resolve().is_relative_to(self.legacy_root):
                raise ValueError("legacy database escapes configured root")
            with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA query_only=ON")
                tables = {row[0].lower(): row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                table = tables.get("images")
                if not table:
                    self._legacy_state = "unsupported_schema"
                    return
                # The only dynamic identifier came from sqlite_master and is
                # escaped. Never read any legacy chat or personal-memory table.
                quoted = '"' + table.replace('"', '""') + '"'
                columns = {row[1] for row in db.execute(f"PRAGMA table_info({quoted})")}
                if not {"description", "full_path"}.issubset(columns):
                    self._legacy_state = "unsupported_schema"
                    return
                for row in db.execute(f"SELECT * FROM {quoted} LIMIT ?", (MAX_ROWS,)):
                    item = dict(row)
                    if item.get("is_banned") or item.get("no_file_flag") or not item.get("is_registered", True):
                        continue
                    curated = self._catalogue.get(_text(item.get("description"), 500))
                    if curated is None:
                        continue
                    path = self._legacy_path(item.get("full_path"))
                    accepted = self._accepted_image(path, curated, "legacy_curated") if path else None
                    if not accepted:
                        self._rejected += 1
                        continue
                    self._images.append(accepted)
                    self._legacy_images += 1
                self._legacy_state = "read_only"
        except (sqlite3.Error, OSError, ValueError):
            self._legacy_state = "unreadable"
            self._errors.append("MaiBot.db: could not read curated image registry")

    async def status(self) -> dict:
        async with self._lock:
            active = {kind: sum(row["enabled"] for row in self._records[kind].values()) for kind in KINDS}
            disabled = {kind: len(self._records[kind]) - active[kind] for kind in KINDS}
            return {"ready": self._opened, "jargon": len(self._jargon), "jargons": len(self._jargon),
                "knowledge": len(self._knowledge), "expressions": len(self._expressions),
                "images": len(self._images), "catalogue_images": len(self._catalogue),
                "legacy": self._legacy_state, "legacy_images": self._legacy_images,
                "rejected": self._rejected, "sources": list(self._sources), "errors": list(self._errors),
                "active": active, "disabled": disabled,
                "total": {kind: len(self._records[kind]) for kind in KINDS}}

    async def lookup(self, query: str, limit: int = 4) -> dict:
        async with self._lock:
            return self._lookup(query, limit)

    def _lookup(self, query: str, limit: int = 4) -> dict:
        query = _text(query, 1000).lower()
        limit = min(max(int(limit), 0), 12)
        tokens = _tokens(query)

        def best(rows: list[dict], primary: str, secondary: str) -> list[dict]:
            scored = [(_score(query, tokens, row.get(primary, ""), row.get(secondary, "")), i, row)
                      for i, row in enumerate(rows)]
            return [dict(row) for score, _, row in sorted(scored, key=lambda item: (-item[0], item[1]))[:limit]
                    if score > 0]

        if not query or not limit:
            return {"jargon": [], "knowledge": [], "expressions": []}
        return {"jargon": best(self._jargon, "text", "meaning"),
                "knowledge": best(self._knowledge, "title", "text"),
                "expressions": best(self._expressions, "situation", "style")}

    async def sample_emoji(self, query: str = "") -> dict | None:
        async with self._lock:
            return await self._offload(self._sample_emoji, query)

    def _sample_emoji(self, query: str = "") -> dict | None:
        query = _text(query, 1000).lower()
        tokens = _tokens(query)
        exact = self._records["images"].get(query)
        scored = [(_score(query, tokens, " ".join(item["tags"]), item["description"]), item)
                  for item in self._images]
        if query.startswith("img_"):
            candidates = [exact] if exact and exact["enabled"] else []
        elif query:
            best = max((score for score, _ in scored), default=0)
            candidates = [item for score, item in scored if score == best and score > 0]
        else:
            candidates = list(self._images)
        random.SystemRandom().shuffle(candidates)
        for item in candidates:
            path = self._record_image_file(item)
            if path is not None:
                return {"id": item["id"], "path": str(path), "description": item["description"],
                        "tags": list(item["tags"]), "source": item["source"]}
        return None

    def _record_image_file(self, item: dict) -> Path | None:
        if item["source"] in {"bundled", "uploaded"}:
            root = self.root
        else:
            if self.legacy_root is None:
                return None
            root = (self.legacy_root / "data" / "emoji").resolve()
            if not root.is_relative_to(self.legacy_root):
                return None
        path = self._image_path(root, item["path"])
        return path if path and self._verify_hash(path, item["sha256"]) else None

    @staticmethod
    def _kind(kind: str) -> str:
        if kind not in KINDS:
            raise ValueError("资源类型无效")
        return kind

    @staticmethod
    def _public(kind: str, row: dict) -> dict:
        return copy.deepcopy({"id": row["id"], "kind": kind, "enabled": row["enabled"],
                              **{key: row[key] for key in PUBLIC_FIELDS[kind]}})

    def _index_base(self) -> None:
        self._base_records = {kind: {} for kind in KINDS}
        for kind, rows in (("images", self._images), ("jargon", self._jargon),
                           ("knowledge", self._knowledge), ("expressions", self._expressions)):
            for original in rows:
                row = copy.deepcopy(original)
                if kind != "images":
                    content = json.dumps({key: row[key] for key in PUBLIC_FIELDS[kind]},
                                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    row["id"] = kind + "_" + hashlib.sha256(content.encode()).hexdigest()[:24]
                row.update(kind=kind, enabled=True)
                self._base_records[kind][row["id"]] = row

    @staticmethod
    def _validate_payload(kind: str, payload: dict, *, partial: bool = False) -> dict:
        if not isinstance(payload, dict) or any(key not in (*PUBLIC_FIELDS[kind], "enabled") for key in payload):
            raise ValueError("资源字段无效")
        result = {}
        defaults = {"title": "", "tags": [], "examples": []}
        maxima = {"description": 500, "text": 100 if kind == "jargon" else 1200,
                  "meaning": 600, "situation": 200, "style": 300, "title": 120}
        for key in PUBLIC_FIELDS[kind]:
            if key not in payload:
                if partial:
                    continue
                if key in defaults:
                    result[key] = copy.deepcopy(defaults[key])
                    continue
                raise ValueError("请填写完整的资源内容")
            value = payload[key]
            if key == "tags":
                if isinstance(value, str):
                    if len(value) > 2400:
                        raise ValueError("图片标签过长")
                    value = _tags(value)
                if (not isinstance(value, list) or len(value) > 40
                        or any(not isinstance(tag, str) or len(tag) > 60 for tag in value)):
                    raise ValueError("图片标签必须为最多40条短文本")
                result[key] = list(dict.fromkeys(tag.strip() for tag in value if tag.strip()))
            elif key == "examples":
                if (not isinstance(value, list) or len(value) > 4
                        or any(not isinstance(example, str) or len(example) > 200 for example in value)):
                    raise ValueError("例句必须为最多4条短文本")
                result[key] = [example.strip() for example in value if example.strip()]
            else:
                if not isinstance(value, str) or len(value) > maxima[key]:
                    raise ValueError("资源内容过长或格式无效")
                value = value.strip()
                if not value and key not in {"title", "description"}:
                    raise ValueError("资源内容不能为空")
                result[key] = value
        if "enabled" in payload:
            if not isinstance(payload["enabled"], bool):
                raise ValueError("启用状态必须为布尔值")
            result["enabled"] = payload["enabled"]
        elif not partial:
            result["enabled"] = True
        return result

    def _load_overlay(self) -> None:
        self._overrides = {kind: {} for kind in KINDS}
        self._upload_records = {}
        path = self.root / "admin-resources.json"
        if not path.exists():
            return
        try:
            if not path.resolve().is_relative_to(self.root) or path.stat().st_size > MAX_JSON_BYTES:
                raise ValueError()
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("items"), dict):
                raise ValueError()
            for kind in KINDS:
                entries = payload["items"].get(kind, {})
                if not isinstance(entries, dict) or len(entries) > MAX_ROWS:
                    raise ValueError()
                for item_id, raw in entries.items():
                    try:
                        if not isinstance(raw, dict):
                            raise ValueError()
                        public = {key: value for key, value in raw.items() if key in (*PUBLIC_FIELDS[kind], "enabled")}
                        normalized = self._validate_payload(kind, public)
                        if item_id not in self._base_records[kind]:
                            if raw.get("_new") is not True:
                                raise ValueError()
                            if kind == "images":
                                relative = raw.get("_path")
                                if not isinstance(relative, str) or not relative.startswith("uploads/"):
                                    raise ValueError()
                                image = self._image_path(self.root, relative)
                                accepted = self._accepted_image(image, {**normalized, "sha256": raw.get("_sha256", "")}, "uploaded") if image else None
                                if not accepted or accepted["id"] != item_id:
                                    raise ValueError()
                                self._upload_records[item_id] = accepted
                                normalized.update(_new=True, _path=relative, _sha256=accepted["sha256"])
                            elif not isinstance(item_id, str) or not re.fullmatch(r"usr_[0-9a-f]{32}", item_id):
                                raise ValueError()
                            else:
                                normalized["_new"] = True
                        self._overrides[kind][item_id] = normalized
                    except (OSError, ValueError, TypeError):
                        self._rejected += 1
            self._sources.append("admin-resources.json")
        except (OSError, ValueError, TypeError, RecursionError):
            self._errors.append("admin-resources.json: 管理资源记录无效或无法读取")

    def _apply_overlay(self) -> None:
        self._records = copy.deepcopy(self._base_records)
        for kind in KINDS:
            for item_id, override in self._overrides[kind].items():
                row = self._records[kind].get(item_id)
                if row is None:
                    if not override.get("_new"):
                        continue
                    row = copy.deepcopy(self._upload_records.get(item_id, {})) if kind == "images" else {}
                row.update({key: copy.deepcopy(override[key]) for key in (*PUBLIC_FIELDS[kind], "enabled") if key in override})
                row.update(id=item_id, kind=kind)
                self._records[kind][item_id] = row
        self._images = [row for row in self._records["images"].values() if row["enabled"]]
        self._jargon = [dict(row, content=row["text"]) for row in self._records["jargon"].values() if row["enabled"]]
        self._knowledge = [dict(row, content=row["text"], meaning=row["text"]) for row in self._records["knowledge"].values() if row["enabled"]]
        self._expressions = [dict(row, text=row["situation"] + "：" + row["style"], meaning=row["style"])
                             for row in self._records["expressions"].values() if row["enabled"]]

    def _persist_overlay(self, overrides: dict) -> None:
        if self.root.resolve() != self.root:
            raise ValueError("资源目录已变化，无法保存")
        self.root.mkdir(parents=True, exist_ok=True)
        destination = self.root / "admin-resources.json"
        if destination.exists() and not destination.resolve().is_relative_to(self.root):
            raise ValueError("管理资源文件路径无效")
        data = json.dumps({"version": 1, "items": overrides}, ensure_ascii=False, indent=2)
        if len(data.encode("utf-8")) > MAX_JSON_BYTES:
            raise ValueError("管理资源数量或内容超出限制")
        temporary = self.root / (".admin-resources-" + uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(0o600)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        self._overrides = overrides
        self._apply_overlay()

    async def list_items(self, kind: str, q: str = "", offset: int = 0, limit: int = 24,
                         include_disabled: bool = False) -> dict:
        kind = self._kind(kind)
        if not isinstance(q, str) or not isinstance(include_disabled, bool):
            raise ValueError("资源查询参数无效")
        try:
            offset, limit = max(int(offset), 0), min(max(int(limit), 1), 100)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("资源分页参数无效") from None
        query = q.strip().lower()[:1000]
        async with self._lock:
            rows = [row for row in self._records[kind].values()
                    if (include_disabled or row["enabled"]) and (not query or query in json.dumps(
                        {key: row[key] for key in PUBLIC_FIELDS[kind]}, ensure_ascii=False).lower())]
            return {"items": [self._public(kind, row) for row in rows[offset:offset + limit]],
                    "total": len(rows), "offset": offset, "limit": limit}

    async def get_item(self, kind: str, item_id: str) -> dict:
        kind = self._kind(kind)
        async with self._lock:
            return self._public(kind, self._records[kind][item_id])

    async def create_item(self, kind: str, payload: dict) -> dict:
        kind = self._kind(kind)
        if kind == "images":
            raise ValueError("图片请通过上传添加")
        normalized = self._validate_payload(kind, payload)
        async with self._lock:
            return await self._offload(self._create_item, kind, normalized)

    def _create_item(self, kind: str, normalized: dict) -> dict:
        if len(self._records[kind]) >= MAX_ROWS:
            raise ValueError("该类资源数量已达上限")
        item_id = "usr_" + uuid.uuid4().hex
        while item_id in self._records[kind]:
            item_id = "usr_" + uuid.uuid4().hex
        overrides = copy.deepcopy(self._overrides)
        overrides[kind][item_id] = {**normalized, "_new": True}
        self._persist_overlay(overrides)
        return self._public(kind, self._records[kind][item_id])

    async def update_item(self, kind: str, item_id: str, patch: dict) -> dict:
        kind = self._kind(kind)
        normalized = self._validate_payload(kind, patch, partial=True)
        async with self._lock:
            return await self._offload(self._update_item, kind, item_id, normalized)

    def _update_item(self, kind: str, item_id: str, patch: dict) -> dict:
        row = self._records[kind][item_id]
        overrides = copy.deepcopy(self._overrides)
        metadata = {key: copy.deepcopy(row[key]) for key in (*PUBLIC_FIELDS[kind], "enabled")}
        previous = overrides[kind].get(item_id, {})
        metadata.update({key: value for key, value in previous.items() if key.startswith("_")})
        metadata.update(patch)
        overrides[kind][item_id] = metadata
        self._persist_overlay(overrides)
        return self._public(kind, self._records[kind][item_id])

    async def delete_item(self, kind: str, item_id: str) -> bool:
        kind = self._kind(kind)
        async with self._lock:
            row = self._records[kind].get(item_id)
            if row is None or not row["enabled"]:
                return False
            await self._offload(self._update_item, kind, item_id, {"enabled": False})
            return True

    @staticmethod
    def _upload_format(filename: str, data: bytes) -> str:
        if (not isinstance(filename, str) or not filename or len(filename) > 160
                or any(char in filename for char in ("/", "\\", ":", "\x00"))
                or filename in {".", ".."}):
            raise ValueError("图片文件名无效")
        if not isinstance(data, (bytes, bytearray)) or not 0 < len(data) <= MAX_IMAGE_BYTES:
            raise ValueError("图片大小须在1字节至5000000字节之间")
        magic = (data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8\xff")
                 or data.startswith((b"GIF87a", b"GIF89a"))
                 or (data.startswith(b"RIFF") and data[8:12] == b"WEBP"))
        if not magic:
            raise ValueError("只支持有效的PNG、JPEG、GIF或WebP图片")
        from io import BytesIO
        from PIL import Image, UnidentifiedImageError
        try:
            with Image.open(BytesIO(data)) as image:
                suffix = {"PNG": ".png", "JPEG": ".jpg", "GIF": ".gif", "WEBP": ".webp"}.get(image.format)
                if not suffix or image.width * image.height > 20_000_000:
                    raise ValueError()
                if ((image.format == "PNG" and not data.endswith(b"\x00\x00\x00\x00IEND\xaeB\x60\x82"))
                        or (image.format == "JPEG" and data.rfind(b"\xff\xd9") < 2)
                        or (image.format == "GIF" and not data.endswith(b";"))
                        or (image.format == "WEBP" and int.from_bytes(data[4:8], "little") + 8 != len(data))):
                    raise ValueError()
                image.verify()
            # Some Pillow verify() implementations only inspect container
            # metadata. Decode the thumbnail frame too; never rewrite bytes.
            with Image.open(BytesIO(data)) as image:
                image.load()
            return suffix
        except (OSError, SyntaxError, ValueError, EOFError, UnidentifiedImageError, Image.DecompressionBombError):
            raise ValueError("图片损坏、格式不支持或像素超过2000万") from None

    async def upload_image(self, filename: str, data: bytes, description: str = "", tags=None) -> dict:
        normalized = self._validate_payload("images", {"description": description, "tags": [] if tags is None else tags})
        async with self._lock:
            return await self._offload(self._upload_image, filename, data, normalized)

    def _upload_image(self, filename: str, data: bytes, normalized: dict) -> dict:
        suffix = self._upload_format(filename, data)
        digest = hashlib.sha256(data).hexdigest()
        item_id = "img_" + digest[:24]
        if item_id in self._records["images"]:
            return self._public("images", self._records["images"][item_id])
        if len(self._records["images"]) >= MAX_ROWS:
            raise ValueError("图片资源数量已达上限")
        self.root.mkdir(parents=True, exist_ok=True)
        directory = self.root / "uploads"
        if not directory.resolve().is_relative_to(self.root):
            raise ValueError("图片保存路径无效")
        directory.mkdir(exist_ok=True)
        path = directory / (digest + suffix)
        if path.exists() and not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError("图片保存路径无效")
        temporary = directory / (".upload-" + uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(0o600)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        overrides = copy.deepcopy(self._overrides)
        overrides["images"][item_id] = {**normalized, "_new": True,
                                        "_path": str(path.relative_to(self.root)).replace("\\", "/"), "_sha256": digest}
        self._upload_records[item_id] = {**normalized, "path": str(path.resolve()), "sha256": digest,
                                         "source": "uploaded", "id": item_id}
        self._persist_overlay(overrides)
        return self._public("images", self._records["images"][item_id])

    async def image_file(self, item_id: str) -> Path | None:
        async with self._lock:
            row = self._records["images"].get(item_id)
            return await self._offload(self._record_image_file, row) if row else None
