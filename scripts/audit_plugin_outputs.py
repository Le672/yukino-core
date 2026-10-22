"""Reproduce the output policy for every shipped plugin and optional sidecar."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from yukino_core.plugins import BUILTINS
from yukino_core.rich_output import BOARD_IDS,CARD_IDS,FILE_IDS


def catalog():
    rows=[]
    records=[dict(row,builtin=True) for row in BUILTINS]
    for path in sorted((ROOT/'extensions').rglob('plugin.json')):
        records.append(dict(json.loads(path.read_text(encoding='utf-8-sig')),builtin=False))
    assert len(records)==136 and len({row['id'] for row in records})==136
    for row in records:
        pid=row['id']
        if pid=='music_search':policy='音频文件、封面、短语音；默认直接播放可用歌曲，支持本人选歌'
        elif pid=='m_bing':policy='每日壁纸原图、标题和版权信息'
        elif pid in ('steam_query','bili_query','m_bangumi','mc_status'):policy='实际查询信息、来源图片/封面与结果卡'
        elif pid=='f1_info':policy='实际赛事数据、可读结果卡与CSV文件'
        elif pid=='m_ics':policy='实际课程日程卡、本周课表与ICS文件导出'
        elif pid=='m_rss':policy='实际订阅标题、日期与源站正文摘要；长内容附卡片/文本'
        elif pid=='s_github':policy='正式发行说明、结果卡及限额内的ZIP/PDF/TXT附件'
        elif pid in BOARD_IDS:policy='真实棋盘/牌面状态图，保留可复制状态和操作文字'
        elif pid in FILE_IDS:policy='实际计算/转换内容、预览和可下载结果文件'
        elif pid in ('emoji','emoji_collector'):policy='原图/动图发送或自动静默收藏；保留原功能'
        elif pid=='hot_trends':policy='编号与标题，不增加链接或图片'
        elif pid in CARD_IDS:policy='实际结果与可读卡片，保留文字'
        elif not row.get('commands'):policy='真实QQ通知与短文案反馈；原事件逻辑保留'
        else:policy='直接给出操作/查询结果；长内容自动排版，超长结果附TXT'
        rows.append({'id':pid,'name':row['name'],'commands':row['commands'],'policy':policy,'scope':'builtin' if row['builtin'] else 'external'})
    rows += [dict(id='jmcomic',name='漫画联动',commands=['/姬漫','/jm'],policy='保留现有搜索和加密ZIP/PDF文件交付；独立服务',scope='deployment integration'),
             dict(id='video_parser',name='视频链接解析',commands=['/视频解析','/解析视频'],policy='保留自动链接解析，合并转发实际封面、视频与评论；独立服务',scope='deployment integration')]
    assert len(rows)==138
    return rows


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=ROOT/'docs/PLUGIN_OUTPUT_MATRIX.md');args=p.parse_args()
    rows=catalog();lines=['# 插件结果输出清单','',
        '0.7.0：逐项覆盖136个公开随包功能，以及现有部署的2个独立集成。表中的保留项已经输出真实结果，不为凑数改成图片。',
        '结果卡直接排版插件返回的真实数据；不会把缺失数据、失败或一个链接包装成成功。此表是功能和交付方式清单，不代表138项都完成了真人QQ逐项实测。','',
        '| 功能 | 指令或触发 | 结果交付方式 |','| --- | --- | --- |']
    for row in rows:lines.append('| '+row['name']+' (`'+row['id']+'`) | '+('、'.join(row['commands']) or '自动事件')+' | '+row['policy']+' |')
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'shipped':136,'deployment_total':138,'output':str(args.output)},ensure_ascii=False))

if __name__=='__main__':main()
