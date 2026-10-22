"""Persistent, scoped conversation state backed by a single SQLite worker."""

from __future__ import annotations

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import re
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable


def _limit(value: int, maximum: int = 500) -> int:
    return min(max(int(value), 0), maximum)


def _session(value: str) -> str:
    if not isinstance(value, str) or not value.startswith(("p:", "g:")) or len(value) < 3:
        raise ValueError("session_id must be p:<user_id> or g:<group_id>")
    return value


def _scope(value: str) -> str:
    return value if value == "global" else _session(value)


def _attachments(value) -> list[dict]:
    if value is None: return []
    if not isinstance(value, list) or len(value) > 4: raise ValueError("invalid attachments")
    result = []
    for item in value:
        if (not isinstance(item, dict) or item.get("type") not in ('image','audio','file') or
                not isinstance(item.get("resource_id"), str) or
                not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", item["resource_id"])):
            raise ValueError("invalid image resource identifier")
        description = item.get("description", "")
        if not isinstance(description, str) or len(description) > 500: raise ValueError("invalid image description")
        row={"type":item['type'], "resource_id": item["resource_id"], "description": description}
        for key in ('filename','mime'):
            if key in item:
                if not isinstance(item[key],str) or len(item[key])>160 or any(ord(c)<32 for c in item[key]):raise ValueError('invalid media metadata')
                row[key]=item[key]
        result.append(row)
    return result


class Store:
    """All connection access, including opening/closing, runs on one worker.

    A reset only advances ``sessions.context_start_id``. Raw messages and event
    receipts remain in the database. ``history(include_archived=True)`` is an
    explicit audit view; normal model context always respects the boundary.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path) if str(path) != ":memory:" else ":memory:"
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="yukino-sqlite")
        self._db: sqlite3.Connection | None = None
        self._closing = False
        self._closed = False

    async def _run(self, operation: Callable[..., Any], *args: Any) -> Any:
        if self._closing or self._closed:
            raise RuntimeError("store is closed")
        return await asyncio.get_running_loop().run_in_executor(self._executor, operation, *args)

    def _connection(self) -> sqlite3.Connection:
        if self._db is None:
            raise RuntimeError("await store.open() before using the store")
        return self._db

    async def open(self) -> None:
        await self._run(self._open)

    def _open(self) -> None:
        if self._db is not None:
            return
        if self.path != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self.path), timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=15000")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY, context_start_id INTEGER NOT NULL DEFAULT 0,
                context_version INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'received', error TEXT NOT NULL DEFAULT '',
                event_json TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                role TEXT NOT NULL, content TEXT NOT NULL, user_id TEXT NOT NULL DEFAULT '',
                display_name TEXT NOT NULL DEFAULT '', message_id TEXT NOT NULL DEFAULT '',
                event_id TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(session_id)
            );
            CREATE INDEX IF NOT EXISTS messages_session_id ON messages(session_id, id);
            CREATE INDEX IF NOT EXISTS messages_bot_id ON messages(session_id, message_id, role);
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, text TEXT NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL, deleted_at REAL
            );
            CREATE INDEX IF NOT EXISTS memories_scope ON memories(scope, deleted_at, id);
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL,
                session_id TEXT NOT NULL, segments_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'prepared', message_id TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL,
                UNIQUE(event_id, session_id)
            );
            CREATE INDEX IF NOT EXISTS outbox_bot_id ON outbox(session_id, message_id, status);
            CREATE TABLE IF NOT EXISTS notice_cooldowns (
                key TEXT PRIMARY KEY, until_at REAL NOT NULL
            );
            PRAGMA user_version=1;
        """)
        columns = {row[1] for row in db.execute("PRAGMA table_info(messages)")}
        if "attachments_json" not in columns:
            db.execute("ALTER TABLE messages ADD COLUMN attachments_json TEXT NOT NULL DEFAULT '[]'")
            # Old image-only sends already have a confirmed QQ message ID. Recover
            # their local asset identity without exposing base64 in history/API.
            for row in db.execute("""SELECT session_id,message_id,segments_json FROM outbox
                WHERE status='sent' AND message_id!='' AND instr(segments_json,'\"image\"')>0"""):
                attachments = []
                try:
                    if len(row["segments_json"]) > 7_000_000: continue
                    for segment in json.loads(row["segments_json"]):
                        data = segment.get("data", {}).get("file", "")
                        if segment.get("type") != "image" or not isinstance(data, str) or not data.startswith("base64://"): continue
                        payload = base64.b64decode(data[9:], validate=True)
                        if not 0 < len(payload) <= 5_000_000: continue
                        attachments.append({"type": "image", "resource_id": "img_" + hashlib.sha256(payload).hexdigest()[:24],
                                            "description": "已发送表情"})
                    if attachments:
                        db.execute("UPDATE messages SET attachments_json=? WHERE session_id=? AND message_id=? AND role='assistant'",
                                   (json.dumps(attachments[:4], ensure_ascii=False), row["session_id"], row["message_id"]))
                except (ValueError, TypeError, KeyError, AttributeError):
                    continue
        db.execute("PRAGMA user_version=2")
        db.commit()
        # A previous process may have died after dispatching a send. The peer
        # could already have received it; never make it eligible for replay.
        with db:
            db.execute("""UPDATE outbox SET status='unknown',
                error='process stopped while send was in progress',updated_at=? WHERE status='sending'""",
                       (time.time(),))
            db.execute("""UPDATE events SET state='interrupted',
                error='服务重启，未完成任务已中断且不会自动重放',updated_at=?
                WHERE state IN ('received','queued','generating')""", (time.time(),))
            db.execute("""UPDATE events SET state='notice_backpoke_unknown',
                error='服务在回戳期间重启；文字已发，回戳不重试',updated_at=?
                WHERE state='notice_backpoke_sending'""", (time.time(),))
        self._db = db

    async def close(self) -> None:
        if self._closed:
            return
        if self._closing:
            return
        self._closing = True
        try:
            await asyncio.get_running_loop().run_in_executor(self._executor, self._close)
        finally:
            await asyncio.to_thread(self._executor.shutdown, wait=True)
            self._closed = True

    def _close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    @staticmethod
    def _touch(db: sqlite3.Connection, session_id: str, now: float) -> None:
        db.execute("""INSERT INTO sessions(session_id, created_at, updated_at) VALUES(?,?,?)
            ON CONFLICT(session_id) DO UPDATE SET updated_at=excluded.updated_at""",
                   (session_id, now, now))

    async def ingest_event(self, event: dict) -> bool:
        # Snapshot caller-owned dictionaries before handing them to the worker.
        payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        return await self._run(self._ingest_event, json.loads(payload), payload)

    def _ingest_event(self, event: dict, payload: str) -> bool:
        session_id = _session(event["session_id"])
        event_id = str(event.get("event_id", ""))
        if not event_id:
            raise ValueError("event_id is required for deduplication")
        kind = event.get("message_type")
        target = event.get("group_id") if kind == "group" else event.get("user_id")
        if kind in {"group", "private"} and session_id != ("g:" if kind == "group" else "p:") + str(target):
            raise ValueError("event target does not match session_id")
        db, now = self._connection(), time.time()
        with db:
            inserted = db.execute("""INSERT OR IGNORE INTO events
                (event_id,session_id,event_json,created_at,updated_at) VALUES(?,?,?,?,?)""",
                                  (event_id, session_id, payload, now, now))
            if inserted.rowcount == 0:
                return False
            if event.get("post_type") == "notice":
                return True
            self._touch(db, session_id, now)
            db.execute("""INSERT INTO messages
                (session_id,role,content,user_id,display_name,message_id,event_id,created_at)
                VALUES(?,'user',?,?,?,?,?,?)""",
                       (session_id, str(event.get("text", "")), str(event.get("user_id", "")),
                        str(event.get("display_name", "")), str(event.get("message_id", "")), event_id, now))
        return True

    async def message_event(self, session_id: str, message_id: str) -> dict | None:
        """Retrieve received segments only from this already-observed conversation."""
        return await self._run(self._message_event, _session(session_id), str(message_id))

    def _message_event(self, session_id: str, message_id: str) -> dict | None:
        row = self._connection().execute("""SELECT e.event_json FROM messages m
            JOIN events e ON e.event_id=m.event_id AND e.session_id=m.session_id
            WHERE m.session_id=? AND m.message_id=? AND m.role='user'
            ORDER BY m.id DESC LIMIT 1""", (session_id, message_id)).fetchone()
        return json.loads(row[0]) if row else None

    async def claim_notice_cooldown(self, limits: dict[str, float]) -> bool:
        """Atomically reserve every notice rate limit, including across restart."""
        clean = {str(key)[:200]: float(seconds) for key, seconds in limits.items()
                 if isinstance(seconds, (int, float)) and 0 < seconds <= 86400}
        return await self._run(self._claim_notice_cooldown, clean)

    def _claim_notice_cooldown(self, limits: dict[str, float]) -> bool:
        db, now = self._connection(), time.time()
        with db:
            for key in limits:
                row = db.execute("SELECT until_at FROM notice_cooldowns WHERE key=?", (key,)).fetchone()
                if row and row[0] > now:
                    return False
            db.execute("DELETE FROM notice_cooldowns WHERE until_at<=?", (now,))
            if db.execute("SELECT count(*) FROM notice_cooldowns").fetchone()[0] + len(limits) > 10000:
                return False
            db.executemany("INSERT OR REPLACE INTO notice_cooldowns(key,until_at) VALUES(?,?)",
                           [(key, now + seconds) for key, seconds in limits.items()])
        return True

    async def add_message(self, session_id: str, role: str, content: str, user_id: str = "",
                          display_name: str = "", message_id: str = "", event_id: str = "", attachments=None) -> int:
        return await self._run(self._add_message, _session(session_id), role, content,
                               str(user_id), display_name, str(message_id), event_id,
                               json.dumps(_attachments(attachments), ensure_ascii=False, separators=(",", ":")))

    def _add_message(self, session_id: str, role: str, content: str, user_id: str,
                     display_name: str, message_id: str, event_id: str, attachments: str) -> int:
        if role not in {"user", "assistant", "system", "tool"}:
            raise ValueError("invalid message role")
        db, now = self._connection(), time.time()
        with db:
            self._touch(db, session_id, now)
            cursor = db.execute("""INSERT INTO messages
                (session_id,role,content,user_id,display_name,message_id,event_id,created_at,attachments_json)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                                (session_id, role, content, user_id, display_name, message_id, event_id, now, attachments))
        return int(cursor.lastrowid)

    async def history(self, session_id: str, limit: int = 16, exclude_event_id: str | None = None,
                      *, include_archived: bool = False, before_event_id: str | None = None) -> list[dict]:
        return await self._run(self._history, _session(session_id), _limit(limit),
                               exclude_event_id, include_archived, before_event_id)

    def _history(self, session_id: str, limit: int, exclude: str | None,
                 archived: bool, boundary: str | None) -> list[dict]:
        db = self._connection()
        rows = db.execute("""WITH cutoff AS (
                SELECT MAX(id) AS id FROM messages WHERE session_id=? AND event_id=?
            ) SELECT m.* FROM messages m JOIN sessions s USING(session_id) CROSS JOIN cutoff c
            WHERE m.session_id=? AND (? OR m.id>s.context_start_id)
            AND (? IS NULL OR m.event_id!=?)
            AND (? IS NULL OR (c.id IS NOT NULL AND (m.role!='user' OR m.id<=c.id)))
            ORDER BY m.id DESC LIMIT ?""",
                          (session_id, boundary, session_id, archived, exclude, exclude, boundary, limit)).fetchall()
        result = []
        for row in reversed(rows):
            item = dict(row)
            try: item["attachments"] = _attachments(json.loads(item.pop("attachments_json", "[]")))
            except (ValueError, TypeError): item["attachments"] = []
            result.append(item)
        return result

    async def mark_event(self, event_id: str, state: str, error: str = "") -> None:
        await self._run(self._mark_event, event_id, state, error)

    def _mark_event(self, event_id: str, state: str, error: str) -> None:
        db = self._connection()
        with db:
            db.execute("UPDATE events SET state=?,error=?,updated_at=? WHERE event_id=?",
                       (state, error, time.time(), event_id))

    async def event_state(self, event_id: str) -> str | None:
        return await self._run(self._event_state, event_id)

    def _event_state(self, event_id: str) -> str | None:
        row = self._connection().execute("SELECT state FROM events WHERE event_id=?", (event_id,)).fetchone()
        return row[0] if row else None

    async def list_sessions(self, limit: int = 80) -> list[dict]:
        return await self._run(self._list_sessions, _limit(limit))

    def _list_sessions(self, limit: int) -> list[dict]:
        rows = self._connection().execute("""SELECT s.*,s.session_id AS id,
            (SELECT count(*) FROM messages m WHERE m.session_id=s.session_id) AS message_count,
            (SELECT content FROM messages m WHERE m.session_id=s.session_id ORDER BY m.id DESC LIMIT 1) AS last_message
            FROM sessions s ORDER BY s.updated_at DESC,s.session_id LIMIT ?""", (limit,)).fetchall()
        return [dict(row) for row in rows]

    async def reset_session(self, session_id: str) -> int:
        return await self._run(self._reset_session, _session(session_id))

    def _reset_session(self, session_id: str) -> int:
        db, now = self._connection(), time.time()
        with db:
            self._touch(db, session_id, now)
            db.execute("""UPDATE sessions SET context_start_id=(
                SELECT COALESCE(MAX(id),0) FROM messages WHERE session_id=?),
                context_version=context_version+1,updated_at=? WHERE session_id=?""",
                       (session_id, now, session_id))
        return int(db.execute("SELECT context_version FROM sessions WHERE session_id=?", (session_id,)).fetchone()[0])

    async def is_bot_message(self, session_id: str, message_id: str) -> bool:
        if not message_id:
            return False
        return await self._run(self._is_bot_message, _session(session_id), str(message_id))

    def _is_bot_message(self, session_id: str, message_id: str) -> bool:
        db = self._connection()
        return bool(db.execute("""SELECT 1 FROM messages WHERE session_id=? AND message_id=?
            AND role='assistant' UNION ALL SELECT 1 FROM outbox
            WHERE session_id=? AND message_id=? AND status='sent' LIMIT 1""",
                               (session_id, message_id, session_id, message_id)).fetchone())

    async def remember(self, scope: str, text: str) -> int:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("memory text is required")
        return await self._run(self._remember, _scope(scope), text.strip())

    def _remember(self, scope: str, text: str) -> int:
        db, now = self._connection(), time.time()
        with db:
            cursor = db.execute("INSERT INTO memories(scope,text,created_at,updated_at) VALUES(?,?,?,?)",
                                (scope, text, now, now))
        return int(cursor.lastrowid)

    async def recall(self, scope: str, query: str = "", limit: int = 8) -> list[dict]:
        return await self._run(self._recall, _scope(scope), query, _limit(limit, 100))

    def _recall(self, scope: str, query: str, limit: int) -> list[dict]:
        # instr treats %, _, quotes and SQL-like input as literal text.
        rows = self._connection().execute("""SELECT *,text AS content FROM memories
            WHERE scope=? AND deleted_at IS NULL AND (?='' OR instr(lower(text),lower(?))>0)
            ORDER BY id DESC LIMIT ?""", (scope, query, query, limit)).fetchall()
        return [dict(row) for row in rows]

    async def forget(self, scope: str, memory_id: int) -> bool:
        return await self._run(self._forget, _scope(scope), int(memory_id))

    def _forget(self, scope: str, memory_id: int) -> bool:
        db, now = self._connection(), time.time()
        with db:
            cursor = db.execute("""UPDATE memories SET deleted_at=?,updated_at=?
                WHERE id=? AND scope=? AND deleted_at IS NULL""", (now, now, memory_id, scope))
        return cursor.rowcount == 1

    async def update_memory(self, scope: str, memory_id: int, text: str) -> bool:
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 1000: raise ValueError("invalid memory text")
        return await self._run(self._update_memory, _scope(scope), int(memory_id), text.strip())

    def _update_memory(self, scope: str, memory_id: int, text: str) -> bool:
        db = self._connection()
        with db:
            row = db.execute("UPDATE memories SET text=?,updated_at=? WHERE id=? AND scope=? AND deleted_at IS NULL",
                             (text, time.time(), memory_id, scope))
        return row.rowcount == 1

    async def outbox_prepare(self, event_id: str, session_id: str, segments: list[dict]) -> dict:
        if not event_id or not isinstance(segments, list):
            raise ValueError("outbox requires an event_id and a segment array")
        payload = json.dumps(segments, ensure_ascii=False, separators=(",", ":"))
        return await self._run(self._outbox_prepare, str(event_id), _session(session_id), payload)

    @staticmethod
    def _outbox_row(row: sqlite3.Row) -> dict:
        result = dict(row)
        result["segments"] = json.loads(result.pop("segments_json"))
        return result

    def _outbox_prepare(self, event_id: str, session_id: str, payload: str) -> dict:
        db, now = self._connection(), time.time()
        with db:
            event = db.execute("SELECT session_id FROM events WHERE event_id=?", (event_id,)).fetchone()
            if event is not None and event["session_id"] != session_id:
                raise ValueError("outbox session does not match the incoming event")
            db.execute("""INSERT OR IGNORE INTO outbox
                (event_id,session_id,segments_json,created_at,updated_at) VALUES(?,?,?,?,?)""",
                       (event_id, session_id, payload, now, now))
        return self._outbox_row(db.execute("SELECT * FROM outbox WHERE event_id=? AND session_id=?",
                                          (event_id, session_id)).fetchone())

    async def outbox_mark(self, outbox_id: int, status: str, message_id: str = "", error: str = "") -> None:
        if status not in {"prepared", "pending", "sending", "sent", "failed", "unknown", "cancelled"}:
            raise ValueError("invalid outbox status")
        await self._run(self._outbox_mark, int(outbox_id), status, str(message_id), error)

    async def compact_media(self,outbox_id,attachments):
        await self._run(self._compact_media,outbox_id,attachments)

    def _compact_media(self,outbox_id,attachments):
        db=self._connection();row=db.execute("SELECT segments_json FROM outbox WHERE id=? AND status='sent'",(outbox_id,)).fetchone()
        if not row:return
        segments=json.loads(row[0]);index=0
        for segment in segments:
            data=segment.get('data',{})
            if segment.get('type') in ('image','record','file') and str(data.get('file','')).startswith('base64://'):
                if index>=len(attachments):return
                data['file']='asset://'+attachments[index]['resource_id'];index+=1
        with db:db.execute("UPDATE outbox SET segments_json=? WHERE id=? AND status='sent'",(json.dumps(segments,ensure_ascii=False),outbox_id))

    async def outbox_claim(self, outbox_id: int) -> bool:
        """Acquire the send once, even when concurrent handlers prepared it."""
        return await self._run(self._outbox_claim, int(outbox_id))

    def _outbox_claim(self, outbox_id: int) -> bool:
        db = self._connection()
        with db:
            cursor = db.execute("UPDATE outbox SET status='sending',updated_at=? WHERE id=? AND status='prepared'",
                                (time.time(), outbox_id))
        return cursor.rowcount == 1

    def _outbox_mark(self, outbox_id: int, status: str, message_id: str, error: str) -> None:
        db = self._connection()
        with db:
            row = db.execute("SELECT status FROM outbox WHERE id=?", (outbox_id,)).fetchone()
            if row is None:
                raise ValueError("unknown outbox id")
            if row["status"] in {"sent", "unknown"} and status not in {row["status"], "sent"}:
                raise ValueError("an acknowledged or uncertain send cannot be replayed")
            db.execute("""UPDATE outbox SET status=?,message_id=CASE WHEN ?!='' THEN ? ELSE message_id END,
                error=?,updated_at=? WHERE id=?""", (status, message_id, message_id, error, time.time(), outbox_id))

    async def list_outbox(self, limit: int = 50) -> list[dict]:
        return await self._run(self._list_outbox, _limit(limit))

    def _list_outbox(self, limit: int) -> list[dict]:
        return [self._outbox_row(row) for row in self._connection().execute(
            "SELECT * FROM outbox ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

    async def recent_events(self, limit: int = 80) -> list[dict]:
        return await self._run(self._recent_events, _limit(limit))

    def _recent_events(self, limit: int) -> list[dict]:
        result = []
        for row in self._connection().execute("SELECT * FROM events ORDER BY created_at DESC,event_id LIMIT ?", (limit,)):
            item = dict(row)
            event = json.loads(item.pop("event_json"))
            item["event"] = event
            for field in ("message_id", "user_id", "group_id", "message_type", "text", "display_name"):
                item[field] = event.get(field, "")
            result.append(item)
        return result

    async def stats(self) -> dict:
        return await self._run(self._stats)

    def _stats(self) -> dict:
        db = self._connection()
        result = {table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                  for table in ("sessions", "messages", "events", "outbox")}
        result["memories"] = db.execute("SELECT count(*) FROM memories WHERE deleted_at IS NULL").fetchone()[0]
        result["deleted_memories"] = db.execute("SELECT count(*) FROM memories WHERE deleted_at IS NOT NULL").fetchone()[0]
        result["outbox_by_status"] = dict(db.execute("SELECT status,count(*) FROM outbox GROUP BY status").fetchall())
        result["events_by_state"] = dict(db.execute("SELECT state,count(*) FROM events GROUP BY state").fetchall())
        return result
