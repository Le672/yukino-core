"""Original bounded public-data plugin; host data_dir is the only state root."""
import importlib.util
from pathlib import Path
KIND='m_webtext'
helper=Path(__file__).with_name('_network.py')
if not helper.exists():
    helper=Path(__file__).parents[2]/'network_engine.py'
spec=importlib.util.spec_from_file_location('yukino_market2_'+KIND,helper)
engine=importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)

def handle(text,context):
    ctx=dict(context) if isinstance(context,dict) else {}
    # Core 0.5 assigns data_dir; map it internally without reading chat content.
    if 'data_dir' in ctx:
        ctx['plugin_data_dir']=ctx['data_dir']
    return engine.handle(KIND,text,ctx)
