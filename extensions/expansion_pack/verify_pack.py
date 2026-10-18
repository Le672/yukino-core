"""Install all 63 packages in an isolated directory and invoke real workers."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parents[1]))
from yukino_core.config import Config
from yukino_core.plugin_packages import install_package
from yukino_core.plugins import PluginManager, BUILTINS


async def verify(directory):
    pack=json.loads((directory/'pack-manifest.json').read_text(encoding='utf-8'))
    assert pack['plugin_count']==63 and len(pack['plugins'])==63
    os.environ['YUKINO_ADMIN_TOKEN']=secrets.token_urlsafe(32);os.environ['YUKINO_SECRETS_FILE']=''
    results=[]
    with tempfile.TemporaryDirectory(prefix='yukino-expansion-') as temporary:
        config=Config(Path(temporary)/'config.json');config.data['plugin_worker_limit']=3
        config.owner_ids=['2'];config.self_id='3';config.save()
        manager=PluginManager(config,Path(temporary)/'plugins')
        try:
            for item in pack['plugins']:
                payload=(directory/item['archive']).read_bytes();assert hashlib.sha256(payload).hexdigest()==item['sha256']
                installed=await install_package(manager,payload);assert installed['enabled'] is False
                manager.update_settings(item['id'],item['settings']);await manager.toggle(item['id'],True)
            assert len(manager.items)==len(BUILTINS)+63
            for item in pack['plugins']:
                command=item['smoke_command'];plugin,_=manager.match(command)
                assert plugin and plugin['id']==item['id'],(item['id'],'routing mismatch')
                start=time.monotonic();reply=await manager.execute(plugin,command,{'session_id':'g:1','user_id':'2'})
                assert item['id'] not in manager.errors,(item['id'],manager.errors)
                assert isinstance(reply,str) and reply.strip(),(item['id'],'empty reply')
                expectations=item.get('expected_contains',[]);expectations=[expectations] if isinstance(expectations,str) else expectations
                assert expectations,(item['id'],'smoke needs semantic expectation')
                for expected in expectations:assert str(expected) in reply,(item['id'],expected,reply)
                assert len(manager.workers)<=3
                results.append({'id':item['id'],'elapsed_ms':round((time.monotonic()-start)*1000),'reply':reply[:1200],
                    'qq_sent':False,'model_called':False})
            again=PluginManager(Config(config.path),manager.root);assert len(again.items)==len(manager.items) and not again.manifest_error
        finally:await manager.close()
    return {'passed':True,'plugins_installed':63,'worker_cases':results,'pool_limit_checked':3,
        'qq_connected':False,'model_called':False,'production_data_used':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--packages',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True);args=parser.parse_args()
    result=asyncio.run(verify(args.packages));args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='worker_cases'}))
