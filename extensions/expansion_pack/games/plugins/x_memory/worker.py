"""Original offline game. No model or network is used."""
import importlib.util
from pathlib import Path
KIND = 'x_memory'
helper=Path(__file__).with_name('_engine.py')
if not helper.exists(): helper=Path(__file__).parents[2]/'games_engine.py'
spec=importlib.util.spec_from_file_location('yukino_expansion_'+KIND,helper)
engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)
def handle(text,context):
    return engine.handle(KIND,text,context)
