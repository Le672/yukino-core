import asyncio
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from yukino_core.store import Store


def incoming(event_id="e1", session_id="p:100", text="你好"):
    group = session_id.startswith("g:")
    return {"event_id": event_id, "session_id": session_id, "message_id": "m" + event_id,
            "user_id": "100", "group_id": session_id[2:] if group else "",
            "self_id": "999", "message_type": "group" if group else "private",
            "text": text, "display_name": "小明", "segments": [{"type": "text", "data": {"text": text}}],
            "mentioned": False, "reply_to": "", "time": 123.0}


class StoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "store.sqlite3"
        self.store = Store(self.path)
        await self.store.open()

    async def asyncTearDown(self):
        await self.store.close()
        self.tmp.cleanup()

    async def test_concurrent_duplicate_is_one_atomic_receipt_and_user_row(self):
        accepted = await asyncio.gather(*(self.store.ingest_event(incoming()) for _ in range(80)))
        self.assertEqual(sum(accepted), 1)
        rows = await self.store.history("p:100")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["content"], "你好")
        self.assertEqual(await self.store.event_state("e1"), "received")
        self.assertEqual((await self.store.stats())["events"], 1)

    async def test_receipt_rolls_back_if_message_write_fails(self):
        with patch.object(Store, "_touch", side_effect=sqlite3.OperationalError("injected write failure")):
            with self.assertRaises(sqlite3.OperationalError):
                await self.store.ingest_event(incoming())
        self.assertIsNone(await self.store.event_state("e1"))
        self.assertTrue(await self.store.ingest_event(incoming()))

    async def test_scopes_and_query_are_literal_and_never_mix(self):
        await self.store.ingest_event(incoming("private"))
        await self.store.ingest_event(incoming("group", "g:100", "群聊内容"))
        await self.store.add_message("p:200", "user", "另一个私聊")
        await self.store.add_message("p:100", "assistant", "私聊回复", message_id="reply")
        self.assertEqual([r["content"] for r in await self.store.history("g:100")], ["群聊内容"])
        self.assertEqual([r["content"] for r in await self.store.history("p:200")], ["另一个私聊"])
        self.assertTrue(await self.store.is_bot_message("p:100", "reply"))
        self.assertFalse(await self.store.is_bot_message("g:100", "reply"))
        private_memory = await self.store.remember("p:100", "喜欢 100% 冰美式")
        await self.store.remember("g:100", "群里的秘密")
        await self.store.remember("global", "显式手工全局知识")
        self.assertEqual(len(await self.store.recall("p:100", "%")), 1)
        self.assertEqual(await self.store.recall("p:200"), [])
        self.assertFalse(await self.store.forget("g:100", private_memory))
        self.assertTrue(await self.store.forget("p:100", private_memory))
        self.assertFalse(await self.store.forget("p:100", private_memory))
        self.assertEqual(await self.store.recall("p:100"), [])
        await self.store.close()
        with closing(sqlite3.connect(self.path)) as db:
            self.assertIsNotNone(db.execute("SELECT deleted_at FROM memories WHERE id=?", (private_memory,)).fetchone()[0])

    async def test_reset_preserves_raw_history_and_dedupe_across_restart(self):
        await self.store.ingest_event(incoming())
        await self.store.add_message("p:100", "assistant", "旧回复")
        await self.store.ingest_event(incoming("g", "g:1", "保留群上下文"))
        version = await self.store.reset_session("p:100")
        self.assertEqual(version, 1)
        self.assertEqual(await self.store.history("p:100"), [])
        self.assertEqual(len(await self.store.history("p:100", include_archived=True)), 2)
        self.assertEqual(len(await self.store.history("g:1")), 1)
        await self.store.ingest_event(incoming("new", text="新消息"))
        self.assertEqual([r["content"] for r in await self.store.history("p:100")], ["新消息"])
        await self.store.close()
        self.store = Store(self.path)
        await self.store.open()
        self.assertFalse(await self.store.ingest_event(incoming()))
        self.assertEqual((await self.store.stats())["messages"], 4)
        self.assertEqual(len(await self.store.history("p:100", include_archived=True)), 3)
        self.assertEqual((await self.store.list_sessions())[0]["context_version"], 1)

    async def test_history_order_limit_and_event_exclusion(self):
        for i in range(6):
            await self.store.add_message("g:1", "user", str(i), event_id=str(i))
        self.assertEqual([r["content"] for r in await self.store.history("g:1", 3, "4")], ["2", "3", "5"])
        self.assertEqual(await self.store.history("g:1", 0), [])
        self.assertEqual([r["content"] for r in await self.store.history("g:1", 10, before_event_id="3")],
                         ["0", "1", "2", "3"])
        self.assertEqual(await self.store.history("g:1", 10, before_event_id="nonexistent"), [])

    async def test_only_one_concurrent_handler_can_claim_prepared_outbox(self):
        row = await self.store.outbox_prepare("claim-event", "p:100", [])
        acquired = await asyncio.gather(*(self.store.outbox_claim(row["id"]) for _ in range(50)))
        self.assertEqual(sum(acquired), 1)
        self.assertEqual((await self.store.list_outbox())[0]["status"], "sending")
        await self.store.outbox_mark(row["id"], "unknown")
        self.assertFalse(await self.store.outbox_claim(row["id"]))

    async def test_busy_chat_cutoff_retains_previous_answer_committed_after_next_arrival(self):
        await self.store.ingest_event(incoming("A", text="第一个问题"))
        await self.store.ingest_event(incoming("B", text="第二个问题"))
        await self.store.add_message("p:100", "assistant", "第一个问题的回答")
        await self.store.ingest_event(incoming("C", text="尚未处理的未来问题"))
        history = await self.store.history("p:100", before_event_id="B", exclude_event_id="B")
        self.assertEqual([(row["role"], row["content"]) for row in history],
                         [("user", "第一个问题"), ("assistant", "第一个问题的回答")])

    async def test_outbox_identity_unknown_and_bot_reply_survive_restart(self):
        await self.store.ingest_event(incoming())
        segments = [{"type": "text", "data": {"text": "回复"}}]
        rows = await asyncio.gather(*(self.store.outbox_prepare("e1", "p:100", segments) for _ in range(30)))
        self.assertEqual(len({row["id"] for row in rows}), 1)
        row = rows[0]
        await self.store.outbox_mark(row["id"], "sending")
        await self.store.close()
        self.store = Store(self.path)
        await self.store.open()
        retry = await self.store.outbox_prepare("e1", "p:100", [{"type": "text", "data": {"text": "不得替换"}}])
        self.assertEqual(retry["id"], row["id"])
        self.assertEqual(retry["status"], "unknown")
        self.assertEqual(retry["segments"], segments)
        with self.assertRaises(ValueError):
            await self.store.outbox_mark(row["id"], "sending")
        with self.assertRaises(ValueError):
            await self.store.outbox_prepare("e1", "g:100", segments)
        await self.store.outbox_mark(row["id"], "sent", "bot-123")
        self.assertTrue(await self.store.is_bot_message("p:100", "bot-123"))
        self.assertFalse(await self.store.is_bot_message("g:100", "bot-123"))
        with self.assertRaises(ValueError):
            await self.store.outbox_mark(row["id"], "unknown")
        self.assertEqual((await self.store.stats())["outbox_by_status"], {"sent": 1})

    async def test_restart_interrupts_unfinished_events_without_replay_or_history_loss(self):
        unfinished = ("received", "queued", "generating")
        terminal = ("replied", "ignored", "provider_failed", "send_unknown")
        for state in unfinished + terminal:
            await self.store.ingest_event(incoming(state, text="原始消息：" + state))
            await self.store.mark_event(state, state, "原有终态说明" if state in terminal else "")
        await self.store.close()
        self.store = Store(self.path)
        await self.store.open()
        audit = {row["event_id"]: row for row in await self.store.recent_events()}
        for state in unfinished:
            self.assertEqual(audit[state]["state"], "interrupted")
            self.assertIn("不会自动重放", audit[state]["error"])
            self.assertEqual(audit[state]["event"]["text"], "原始消息：" + state)
            self.assertFalse(await self.store.ingest_event(incoming(state)))
        for state in terminal:
            self.assertEqual(audit[state]["state"], state)
            self.assertEqual(audit[state]["error"], "原有终态说明")
        self.assertEqual(len(await self.store.history("p:100")), len(unfinished + terminal))
        self.assertEqual(await self.store.list_outbox(), [])
        self.assertEqual((await self.store.stats())["events_by_state"]["interrupted"], 3)
        self.assertTrue(await self.store.ingest_event(incoming("fresh-after-restart")))
        self.assertEqual(await self.store.event_state("fresh-after-restart"), "received")

    async def test_event_audit_and_worker_do_not_block_event_loop(self):
        await self.store.ingest_event(incoming())
        await self.store.mark_event("e1", "failed", "provider timeout")
        event = (await self.store.recent_events())[0]
        self.assertEqual(event["state"], "failed")
        self.assertEqual(event["error"], "provider timeout")
        self.assertEqual(event["event"]["segments"][0]["data"]["text"], "你好")
        self.assertGreater(event["updated_at"], 0)
        worker = asyncio.create_task(self.store._run(time.sleep, .12))
        await asyncio.sleep(.02)
        self.assertFalse(worker.done(), "SQLite worker should yield to the event loop")
        await worker

    async def test_rejects_mismatched_event_target_before_persisting(self):
        event = incoming()
        event["session_id"] = "g:100"
        with self.assertRaises(ValueError):
            await self.store.ingest_event(event)
        self.assertEqual((await self.store.stats())["messages"], 0)


if __name__ == "__main__":
    unittest.main()
