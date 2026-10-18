"""Regenerate all 21 wrappers and documentation from the reviewed helpers."""
import importlib.util
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent

def generate():
    catalog=[]
    for name in ('helper_board','helper_extra','helper_puzzles'):
        spec=importlib.util.spec_from_file_location(name,ROOT/(name+'.py'))
        mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        catalog.extend(mod.CATALOG)
    if len(catalog)!=21 or len({i['id'] for i in catalog})!=21: raise ValueError('需要21项独立游戏。')
    for item in catalog:
        folder=ROOT/'plugins'/item['id'];folder.mkdir(parents=True,exist_ok=True)
        usage=item.get('usage','')
        if not usage: raise ValueError(item['id']+' 缺少操作说明。')
        manifest={'id':item['id'],'name':item['name'],'version':'1.0.0',
            'description':usage[:490],'worker':'worker.py','commands':item['commands'],
            'config_schema':item.get('config_schema',[])}
        (folder/'plugin.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        code='''"""Original offline game. No model or network is used."""
import importlib.util
from pathlib import Path
KIND = %r
helper=Path(__file__).with_name('_engine.py')
if not helper.exists(): helper=Path(__file__).parents[2]/'games_engine.py'
spec=importlib.util.spec_from_file_location('yukino_expansion_'+KIND,helper)
engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)
def handle(text,context):
    return engine.handle(KIND,text,context)
'''%item['id']
        (folder/'worker.py').write_text(code,encoding='utf-8')
        multiplayer=item['id'] in {'x_connect4','x_tictactoe','x_gomoku','x_reversi'}
        scoping='双人对局仅在同一群聊中共享；创建者占玩家1座位，另一人发送 加入 成为玩家2。只有落子方可行动，旁观者不可代走、结束或重开进行中的桌。井字棋另有“开始 人机”，支持私聊；私聊不创建等待别人加入的空桌。' if multiplayer else '每个会话中的每位用户拥有独立对局，别人不能读取、操作或结束你的游戏。'
        config='\n'.join('- `'+p['key']+'`：'+p['label']+'，默认 '+str(p.get('default'))+'，范围 '+str(p.get('min'))+'—'+str(p.get('max'))+'。新局采用修改后的参数，当前局继续沿用开局参数。' for p in item.get('config_schema',[])) or '本游戏采用固定棋盘/固定规则，未提供无效的装饰参数。'
        doc=f'''# {item['name']}

插件 ID：`{item['id']}` · 版本：1.0.0 · MIT

## 操作

{usage}

开始示例：`{item['smoke_command']}`。发送 `{item['commands'][0]} 帮助` 随时查看具体规则。

## 面板配置

{config}

## 数据与边界

{scoping}

进度保存在宿主分配的 `data_dir/games.sqlite3`，每一步在 SQLite 事务中校验并写入，非法输入不会消耗回合。worker 退出、进程池回收或内核重启后可继续。短期对局保留到最后操作后31天，每个插件最多保存5000份对局，超过时清理最久未操作的记录；不修改聊天历史或其他插件数据。没有联网、模型调用、主动QQ消息、金钱下注或第三方素材。

本插件为 Yukino 独立实现，需要使用随包提供的 `_engine.py` 与 `helper_*.py`。规则与商业游戏产品可能不同，以以上说明为准。
'''
        (folder/'README.md').write_text(doc,encoding='utf-8')
    (ROOT/'catalog.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    rows='\n'.join('| '+i['name']+' | `'+i['smoke_command']+'` |' for i in catalog)
    (ROOT/'README.md').write_text('''# 离线文字游戏扩展（21项）

全部为真实可玩的独立插件，使用相同事务式持久化基础，各ZIP拥有单独ID、指令和数据目录。具体操作和真实生效参数见各包README或发送对应的“帮助”指令。

| 游戏 | 开局指令 |
| --- | --- |
'''+rows+'''

四子棋、井字棋、九路五子棋、六路黑白棋仅在群聊共享双人桌；每个座位在加入后锁定，旁观者只能看局。井字棋的人机模式也支持私聊。其余17项按会话+用户隔离，不会向另一位QQ用户泄露答案或进度。

每次操作由SQLite事务串行化，非法输入回滚。数据在data_dir/games.sqlite3，worker回收和重启后保留。短期对局在31天未操作后清理，每插件最多5000桌，超出时清理最久未操作记录；聊天、长期记忆和其他插件数据不受影响。

24点采用受限AST与Fraction精确分数，拒绝代码、调用、属性访问、乘方及重复/遗漏数字；每题用求解器确认存在解。所有游戏无需模型、联网、外部账号或付费资源，没有金钱下注或自动QQ发送。成语接龙只使用有限人工整理词表，未收录不等于不是成语；知识题是稳定常识。固定尺寸棋类不声称完整国际竞赛规则。

开发：修改helper_board.py/helper_extra.py/helper_puzzles.py后运行python generate_catalog.py同步清单、21份wrapper和说明；总包构建器会嵌入引擎与固定helper文件。测试位于tests/，包括棋规、题解、错误回滚、状态隐藏和用户/会话隔离。
''',encoding='utf-8')
    return catalog

if __name__=='__main__': print('generated',len(generate()),'game plugins')
