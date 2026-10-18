import base64
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from yukino_core.store import Store


class StoreMediaTests(unittest.IsolatedAsyncioTestCase):
    async def test_migrates_acknowledged_legacy_image_sends_and_keeps_raw_history(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'old.db'
            payload = b'legacy-image-bytes'
            with closing(sqlite3.connect(path)) as db:
                db.executescript('''
                    CREATE TABLE sessions(session_id TEXT PRIMARY KEY,context_start_id INTEGER DEFAULT 0,context_version INTEGER DEFAULT 0,created_at REAL,updated_at REAL);
                    CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,user_id TEXT DEFAULT '',display_name TEXT DEFAULT '',message_id TEXT DEFAULT '',event_id TEXT DEFAULT '',created_at REAL);
                    CREATE TABLE outbox(id INTEGER PRIMARY KEY,event_id TEXT,session_id TEXT,segments_json TEXT,status TEXT,message_id TEXT,error TEXT DEFAULT '',created_at REAL,updated_at REAL,UNIQUE(event_id,session_id));
                    INSERT INTO sessions VALUES('p:100',0,0,1,1);
                    INSERT INTO messages VALUES(1,'p:100','assistant','[表情图片]','999','雪乃','qq-image','',1);
                ''')
                segments=[{'type':'image','data':{'file':'base64://'+base64.b64encode(payload).decode()}}]
                db.execute("INSERT INTO outbox VALUES(1,'e1','p:100',?,'sent','qq-image','',1,1)",(json.dumps(segments),))
                db.commit()
            store=Store(path)
            await store.open()
            history=await store.history('p:100')
            self.assertEqual(history[0]['content'],'[表情图片]')
            self.assertEqual(history[0]['attachments'][0]['resource_id'],'img_'+hashlib.sha256(payload).hexdigest()[:24])
            self.assertNotIn('base64',json.dumps(history))
            await store.close()
            store=Store(path)
            await store.open()
            self.assertEqual(await store.history('p:100'),history)
            await store.close()

    async def test_invalid_attachment_paths_rejected_and_unknown_fields_not_saved(self):
        store=Store(':memory:')
        await store.open()
        try:
            for rid in ['../../key.json','https://evil/image','a?token=secret']:
                with self.assertRaises(ValueError):
                    await store.add_message('p:100','assistant','x',attachments=[{'type':'image','resource_id':rid}])
            await store.add_message('p:100','assistant','x',attachments=[{'type':'image','resource_id':'img_valid','description':'猫','file':'base64://PRIVATE'}])
            row=(await store.history('p:100'))[0]
            self.assertEqual(row['attachments'],[{'type':'image','resource_id':'img_valid','description':'猫'}])
            self.assertNotIn('PRIVATE',json.dumps(row))
        finally: await store.close()
