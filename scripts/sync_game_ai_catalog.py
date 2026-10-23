"""Run after regenerating a catalog to retain the original games' AI adapter opt-in."""
from pathlib import Path
import json,sys
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from yukino_core.game_catalog import GAMES,MULTIPLAYER


def update(record):
    if not isinstance(record,dict) or record.get('id') not in GAMES:return False
    record.update(version='1.3.0',minimum_core_version='0.10.0',game_ai_adapter=1)
    if record['id']=='i_undercover':
        record['description']='谁是卧底：创建、加入、开始；每人私聊取词。AI模式按顺序描述，可正常发言或发 描述 内容；全部描述后 投票 QQ/AI，齐票自动结算。3—12人含一名AI，平票重投。AI只知道自己的词。状态、AI退出、结束。'
    elif record['id']=='i_turtle':
        record['description']='海龟汤：真人主持人私聊设题并在群里开始；玩家提问，主持人 下一问、回答 是/否/无关、揭底。支持AI作为解谜玩家，只看公开汤面与问答，不知道汤底。'
    elif '支持AI' not in record.get('description',''):
        record['description']=record.get('description','')[:440]+' 支持AI'+('玩家' if record['id'] in MULTIPLAYER or record['id']=='rps' else '搭档')+'；创建/开始加 人机 或 加入AI，之后自动行动。'
    if 'usage' in record and '加入AI' not in record['usage']:record['usage']+=' 支持 加入AI、AI状态、AI退出；创建/开始加 人机，或在插件设置启用自动加入。'
    return True


def main():
    changed=[]
    for path in (ROOT/'extensions').rglob('plugin.json'):
        record=json.loads(path.read_text(encoding='utf-8'))
        if update(record):
            path.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n');changed.append(record['id'])
            readme=path.with_name('README.md');text=readme.read_text(encoding='utf-8')
            if '## 模型参与（Core 0.10.0）' not in text:
                text+='\n\n## 模型参与（Core 0.10.0）\n\n本插件声明原生游戏AI适配器。创建/开始加“人机”或发送 `'+record['commands'][0]+' 加入AI`，之后由内核按回合自动调用当前会话模型。插件管理可开启“开新局时自动邀请AI”；AI退出、AI状态、AI重试可用于本局管理。单人题目采用搭档方式，每次真人动作后最多协作一步。完整规则及隐藏信息边界见[AI游戏指南](https://github.com/Le672/yukino-core/blob/main/docs/GAME_AI.md)。本地规则引擎本身不联网；模型调用由内核管理。\n'
                readme.write_text(text,encoding='utf-8',newline='\n')
    assert set(changed)==set(GAMES) and len(changed)==41
    for path in (ROOT/'extensions').rglob('*catalog*.json'):
        data=json.loads(path.read_text(encoding='utf-8'));rows=data if isinstance(data,list) else data.get('plugins',[]) if isinstance(data,dict) else []
        modified=False
        for row in rows:modified=update(row) or modified
        if modified:path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps({'game_adapters':len(changed)},ensure_ascii=False))

if __name__=='__main__':main()
