"""Verify all eight actual worker packages in an isolated instance, without QQ."""
import argparse,asyncio,hashlib,json,os,secrets,sys,tempfile,time
from pathlib import Path

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parents[1]))
from yukino_core.config import Config
from yukino_core.plugins import PluginManager,BUILTINS
from yukino_core.plugin_packages import install_package

async def verify(packages):
    pack=json.loads((packages/'pack-manifest.json').read_text(encoding='utf-8'))
    assert pack['plugin_count']==8 and len(pack['plugins'])==8
    os.environ['YUKINO_SECRETS_FILE']='';os.environ['YUKINO_ADMIN_TOKEN']=secrets.token_urlsafe(32)
    results=[]
    with tempfile.TemporaryDirectory(prefix='yukino-market2-') as folder:
        root=Path(folder);c=Config(root/'config.json');c.owner_ids=['2'];c.self_id='3'
        c.data['plugin_worker_limit']=3;c.save();m=PluginManager(c,root/'plugins')
        try:
            for entry in pack['plugins']:
                payload=(packages/entry['archive']).read_bytes()
                assert hashlib.sha256(payload).hexdigest()==entry['sha256']
                result=await install_package(m,payload);assert result['enabled'] is False
                m.update_settings(entry['id'],entry['settings']);await m.toggle(entry['id'],True)
            assert len(m.items)==len(BUILTINS)+8 and not m.manifest_error
            for entry in pack['plugins']:
                plugin,_=m.match(entry['smoke_command'])
                assert plugin and plugin['id']==entry['id'],(entry['id'],'routing mismatch')
                ctx={'session_id':'g:1','user_id':'2',**entry.get('smoke_context',{})}
                start=time.monotonic();reply=await m.execute(plugin,entry['smoke_command'],ctx)
                expected=entry['expected_contains'];expected=[expected] if isinstance(expected,str) else expected
                assert expected and isinstance(reply,str) and all(value in reply for value in expected),(entry['id'],reply)
                assert entry['id'] not in m.errors and len(m.workers)<=3
                results.append({'id':entry['id'],'reply':reply[:1600],'elapsed_ms':round((time.monotonic()-start)*1000),
                                'qq_sent':False,'model_called':False})
            reloaded=PluginManager(Config(c.path),m.root)
            assert len(reloaded.items)==len(m.items) and not reloaded.manifest_error
        finally:await m.close()
    return {'passed':True,'worker_count':len(results),'results':results,'pool_limit':3,
            'qq_connected':False,'model_called':False,'live_instance_used':False}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--packages',required=True,type=Path)
    parser.add_argument('--report',required=True,type=Path);args=parser.parse_args()
    report=asyncio.run(verify(args.packages))
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='results'}))
