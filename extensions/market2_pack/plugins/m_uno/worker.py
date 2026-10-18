"""Original text-only multiplayer card game worker."""
import importlib.util
from pathlib import Path

helper=Path(__file__).with_name('_engine.py')
if not helper.exists(): helper=Path(__file__).parents[2]/'uno_engine.py'
spec=importlib.util.spec_from_file_location('yukino_market2_uno',helper)
engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)

def handle(text,context):
    return engine.handle(text,context)
