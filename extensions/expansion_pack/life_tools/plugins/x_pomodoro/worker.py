"""Original Yukino worker wrapper; the package builder embeds _engine.py."""
import importlib.util
from pathlib import Path
KIND='x_pomodoro'
COMMANDS=('/番茄钟',)
helper=Path(__file__).with_name('_engine.py')
if not helper.exists():
    helper=Path(__file__).parents[2]/'life_engine.py'
spec=importlib.util.spec_from_file_location('yukino_life_'+KIND,helper)
engine=importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)

def handle(text,context):
    if not isinstance(text,str) or not text.strip() or text.strip().split()[0] not in COMMANDS:
        return None
    return engine.handle(KIND,text,context)
