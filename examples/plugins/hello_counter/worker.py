from pathlib import Path
import sqlite3


def handle(text, context):
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return "用法：/hello 名字，或 /hello_count 查看次数。"
    command = parts[0]
    if command not in ("/hello", "/hello_count"):
        return "用法：/hello 名字，或 /hello_count 查看次数。"

    settings = context.get("settings", {})
    session_id = str(context["session_id"])
    user_id = str(context["user_id"])
    database = Path(context["data_dir"]) / "counter.sqlite3"
    db = sqlite3.connect(database, timeout=1.0)
    try:
        with db:
            db.execute("""CREATE TABLE IF NOT EXISTS counters (
                session_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                total INTEGER NOT NULL,
                PRIMARY KEY (session_id, user_id)
            )""")
            if command == "/hello":
                db.execute("""INSERT INTO counters VALUES (?, ?, 1)
                    ON CONFLICT(session_id, user_id)
                    DO UPDATE SET total=total+1""", (session_id, user_id))
            row = db.execute(
                "SELECT total FROM counters WHERE session_id=? AND user_id=?",
                (session_id, user_id),
            ).fetchone()
            count = row[0] if row else 0
    finally:
        db.close()

    if command == "/hello_count":
        return f"你在这个会话里已使用问候 {count} 次。"
    name = parts[1] if len(parts) > 1 else "朋友"
    name = " ".join(name.split())[:int(settings.get("name_max_chars", 40))]
    greeting = str(settings.get("greeting", "你好"))
    reply = f"{greeting}，{name}。"
    if settings.get("style", "warm") == "warm":
        reply += " 今天也要顺顺利利。"
    if settings.get("show_count", True):
        reply += f"\n这是你在本会话的第 {count} 次问候。"
    return reply
