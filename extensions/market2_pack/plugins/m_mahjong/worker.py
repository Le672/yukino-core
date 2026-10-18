"""Original Yukino worker wrapper; the pack embeds _engine.py."""
import importlib.util
from pathlib import Path
COMMANDS = ('/听牌', '/和牌', '/麻将训练')
helper=Path(__file__).with_name('_engine.py')
if not helper.is_file():
    helper=Path(__file__).parents[2]/'mahjong_engine.py'
spec=importlib.util.spec_from_file_location('yukino_market2_m_mahjong',helper)
engine=importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)

def handle(text,context):
    if not isinstance(text,str) or not text.strip() or text.strip().split()[0] not in COMMANDS:
        return None
    return engine.handle(text,context)
