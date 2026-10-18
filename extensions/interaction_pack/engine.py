"""Separate workers share implementation code, never their state directories."""
import json
from pathlib import Path
from common import execute
import social,boards

def handle(pid,text,context):
    catalog=json.loads(Path(__file__).with_name('catalog.json').read_text(encoding='utf-8'))
    spec=next(p for p in catalog if p['id']==pid)
    handler=boards.dispatch if pid in ('i_dots','i_nim','i_set','i_auction') else social.dispatch
    return execute(pid,text,context,handler,spec['usage'])
