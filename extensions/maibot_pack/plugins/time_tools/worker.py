import importlib.util
from pathlib import Path
KIND = 'time_tools'
COMMANDS = ('/时间', '/倒数')
helper=Path(__file__).with_name('_local.py')
if not helper.exists():helper=Path(__file__).parents[2]/'local_engine.py'
spec=importlib.util.spec_from_file_location('yukino_mai_'+KIND,helper)
local=importlib.util.module_from_spec(spec);spec.loader.exec_module(local)
def handle(text,context):
    if not isinstance(text,str) or not text.strip() or text.strip().split()[0] not in COMMANDS:return None
    return local.handle(KIND,text,context)
