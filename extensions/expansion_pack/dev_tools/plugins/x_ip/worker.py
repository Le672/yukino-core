import importlib.util
from pathlib import Path
KIND = 'x_ip'
COMMANDS = ('/IP信息', '/IP查询', '/IP', '/DNS', '/DNS查询', '/域名查询', '/ip', '/dns')
helper = Path(__file__).with_name('_engine.py')
if not helper.exists():
    helper = Path(__file__).parents[2] / 'dev_engine.py'
spec = importlib.util.spec_from_file_location('yukino_expansion_' + KIND, helper)
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)

def handle(text, context):
    if not isinstance(text, str) or not text.strip() or text.strip().split()[0] not in COMMANDS:
        return None
    return engine.handle(KIND, text, context)
