from __future__ import annotations

import asyncio
import copy
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from .notice_plugins import NOTICE_PLUGINS, NOTICE_FIELDS, validate_notice_setting
from .plugin_results import validate_result, music_intent
from . import __version__
from .game_catalog import GAMES, FIELDS as GAME_FIELDS, capability as game_capability

MAX_EXTERNAL_PLUGINS = 128
MAX_MANIFEST_BYTES = 1_048_576

BUILTINS = [
    {"id": "utilities", "name": "基础工具", "description": "帮助、延迟与状态查询", "commands": ["/帮助", "/ping", "/状态"]},
    {"id": "sessions", "name": "会话管理", "description": "上下文清空边界，保留原始历史", "commands": ["/新对话"]},
    {"id": "memory", "name": "会话记忆", "description": "分会话保存与删除记忆", "commands": ["/记住", "/记忆", "/忘记"]},
    {"id": "emoji", "name": "表情资源", "description": "查询并发送现有表情图片", "commands": ["/表情"]},
    {"id": "emoji_collector", "name": "偷表情包", "description": "静默收集聊天表情、引用图片手动收藏，自动去重并存入资源库", "commands": ["/偷表情", "/偷表情包"]},
    {"id": "knowledge", "name": "中文梗与资料", "description": "本地检索已导入的中文梗与知识", "commands": ["/资料", "/梗"]},
    {"id": "dice", "name": "随机骰子", "description": "本地掷骰，无需模型", "commands": ["/骰子"]},
    {"id": "calculator", "name": "计算器", "description": "独立进程的安全算式插件", "commands": ["/计算"], "worker": "calculator.py"},
    {"id": "group_admin", "name": "群管与管理员", "description": "分级权限、群信息、禁言解禁、撤回、群名片和主人管理指令", "commands": ["/群管","/管理员","/管理","/群信息","/成员","/禁言","/解禁","/全员禁言","/解除全禁","/踢出","/撤回","/群名片","/群名称","/设管理员","/撤管理员"]},
    {"id": "reminders", "name": "持久化定时提醒", "description": "明确时间设定提醒、查询取消，重启保留；发送不确定时不自动重发", "commands": ["/提醒"]},
    {"id": "keyword_reply", "name": "关键词自动回复", "description": "主人配置精确或包含规则，不经过模型，带会话冷却", "commands": ["/关键词"]},
]
BUILTINS += NOTICE_PLUGINS

COMMON_FIELDS = [dict(key='timeout', type='number', label='执行超时（秒）', min=0.2, max=60, default=2),
                 dict(key='input_max_chars', type='integer', label='输入字符上限', min=100, max=8000, default=4000),
                 dict(key='output_max_chars', type='integer', label='输出字符上限', min=100, max=8000, default=4000)]
OUTPUT_FIELDS = [dict(key='output_mode',type='string',label='结果展示方式',default='auto',options=['auto','text','card'],description='自动按功能发送图片或结果卡；音频和文件保留实际内容'),
                 dict(key='export_result',type='boolean',label='长结果与数据工具附带可下载文件',default=True)]
BUILTIN_FIELDS = {
 'emoji_collector':[
    dict(key='auto_collect',type='boolean',label='自动收集聊天中的表情',default=True),
    dict(key='in_groups',type='boolean',label='允许群聊收集（无需@）',default=True),
    dict(key='in_private',type='boolean',label='允许私聊收集',default=True),
    dict(key='stickers_only',type='boolean',label='自动收集仅限标记为表情的图片',default=True),
    dict(key='per_message_limit',type='integer',label='每条消息最多收集图片数',min=1,max=5,default=3),
    dict(key='cooldown_seconds',type='integer',label='同会话自动收集间隔（秒）',min=0,max=3600,default=1),
    dict(key='max_image_mb',type='integer',label='单张图片最大MB',min=1,max=5,default=5),
    dict(key='daily_limit',type='integer',label='每日收集尝试上限（UTC+8）',min=1,max=10000,default=200),
    dict(key='max_collected',type='integer',label='累计收集数量上限',min=1,max=10000,default=2000),
    dict(key='max_total_mb',type='integer',label='累计收集容量上限MB',min=1,max=2048,default=256)],
 'dice':[dict(key='max_count', type='integer', label='最多骰子数', min=1, max=100, default=20),
         dict(key='max_faces', type='integer', label='最大面数', min=2, max=1000000, default=100000)],
 'memory':[dict(key='max_length', type='integer', label='单条记忆字符上限', min=1, max=1000, default=1000),
           dict(key='list_limit', type='integer', label='指令展示记忆条数', min=1, max=100, default=20)],
 'knowledge':[dict(key='result_limit', type='integer', label='每类检索条数', min=1, max=12, default=4),
              dict(key='response_chars', type='integer', label='资料回复字符上限', min=100, max=8000, default=2400)],
 'utilities':[dict(key='help_footer', type='string', label='帮助页附言', default='私聊直接说，群聊@我、回复我或用名字开头。', max_length=1000)],
 'sessions':[dict(key='reset_reply', type='string', label='新对话确认文字', default='好啦，从这句开始重新聊。', max_length=500)]}
BUILTIN_FIELDS.update({
 'group_admin':[
    dict(key='operators',type='string',label='机器人授权群管QQ（每行一个）',default='',max_length=3000),
    dict(key='allow_group_admins',type='boolean',label='允许QQ实际群主/管理员操作本群',default=True),
    dict(key='public_queries',type='boolean',label='群员可查询本群信息和成员公开资料',default=True),
    dict(key='allow_kick',type='boolean',label='允许授权管理员踢人',default=True),
    dict(key='allow_admin_changes',type='boolean',label='允许QQ管理员变更（需机器人为群主）',default=False),
    dict(key='max_ban_seconds',type='integer',label='单次禁言秒数上限',min=1,max=2592000,default=86400)],
 'reminders':[
    dict(key='utc_offset_hours',type='integer',label='提醒时区UTC偏移小时',min=-12,max=14,default=8),
    dict(key='per_user_limit',type='integer',label='每用户每会话待处理提醒上限',min=1,max=50,default=10),
    dict(key='max_days',type='integer',label='最多提前多少天设提醒',min=1,max=365,default=365),
    dict(key='overdue_grace_minutes',type='integer',label='离线后补发宽限分钟',min=1,max=1440,default=60)],
 'keyword_reply':[
    dict(key='rules_json',type='string',label='规则JSON数组（key/reply/mode）',default='[]',max_length=60000),
    dict(key='in_groups',type='boolean',label='群聊启用自动匹配',default=True),
    dict(key='in_private',type='boolean',label='私聊启用自动匹配',default=True),
    dict(key='cooldown_seconds',type='integer',label='同会话自动回复冷却秒数',min=0,max=3600,default=10)]})
BUILTIN_FIELDS.update(NOTICE_FIELDS)


class PluginManager:
    def __init__(self, config, root=None):
        self.config = config
        self.root = Path(root or Path(__file__).with_name("plugins"))
        self.items = [dict(x) for x in BUILTINS]
        self.manifest_error = ""
        # Only administrator-created manifests from the private plugin directory are loaded.
        manifest = self.root / "manifest.json"
        if manifest.exists():
            try:
                if manifest.stat().st_size > MAX_MANIFEST_BYTES: raise ValueError()
                records = json.loads(manifest.read_text(encoding="utf-8"))
                if not isinstance(records, list) or len(records) > MAX_EXTERNAL_PLUGINS: raise ValueError()
                for item in records:
                    if not isinstance(item, dict) or not all(isinstance(item.get(k), str) and item[k] for k in ("id", "name", "description", "worker")):
                        self.manifest_error = "忽略无效插件记录"; continue
                    if any(x["id"] == item["id"] for x in self.items): continue
                    path = (self.root / item["worker"]).resolve()
                    if not path.is_relative_to(self.root.resolve()) or path.suffix != ".py":
                        self.manifest_error = "忽略越界插件路径"; continue
                    if not isinstance(item.get("commands"), list) or not item["commands"] or any(not isinstance(c, str) or not c.startswith("/") or len(c) > 40 for c in item["commands"]):
                        self.manifest_error = "忽略无效插件命令"; continue
                    hosts = item.get("auto_url_hosts", [])
                    paths = item.get("auto_url_paths", {})
                    if (not isinstance(hosts, list) or len(hosts) > 16
                            or any(not isinstance(host, str) or not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)+", host)
                                   or len(host) > 253 for host in hosts)
                            or not isinstance(paths, dict) or any(host not in hosts for host in paths)
                            or any(not isinstance(prefix, str) or not re.fullmatch(r"/[A-Za-z0-9_/-]{0,79}", prefix) for prefix in paths.values())
                            or not isinstance(item.get("silent_ack", False), bool)):
                        self.manifest_error = "忽略无效自动链接配置"; continue
                    record = {k: item[k] for k in ("id", "name", "description", "commands", "worker")}
                    if hosts: record["auto_url_hosts"] = list(dict.fromkeys(hosts))
                    if paths: record["auto_url_paths"] = dict(paths)
                    if item.get("silent_ack"): record["silent_ack"] = True
                    if isinstance(item.get('config_schema'), list): record['config_schema'] = item['config_schema'][:30]
                    if item.get('game_ai_adapter')==1 and item['id'] in GAMES:record['game_ai_adapter']=1
                    for field in ('version','minimum_core_version','result_protocol'):
                        if field in item:record[field]=item[field]
                    self.items.append(record)
            except (OSError, ValueError, TypeError): self.manifest_error = "外部插件清单无效，内置功能仍可使用"
        self.workers, self.locks, self.errors = {}, {}, {}
        self.worker_used = {}
        self.pool_lock = asyncio.Lock()
        self.mutating = False

    def enabled(self, name):
        item = next((item for item in self.items if item['id'] == name), {})
        return self.config.plugin_flags.get(name, item.get('default_enabled', True))

    def list(self):
        return [{**{k: v for k, v in i.items() if k not in ("worker", "config_schema")}, "enabled": self.enabled(i["id"]),
                 'builtin': i['id'] in {b['id'] for b in BUILTINS},
                 'game_ai':game_capability(i['id']) if i.get('game_ai_adapter')==1 else None,
                 'configurable': bool(self.get_settings(i['id'])['schema']['fields']),
                 "type": "process" if "worker" in i else "native", "state": "failed" if i["id"] in self.errors else ("ready" if self.enabled(i["id"]) else "disabled"),
                 "error": self.errors.get(i["id"], "")} for i in self.items]

    async def toggle(self, name, enabled):
        if self.mutating: raise ValueError('插件配置正在更新，请稍后修改')
        if not any(i["id"] == name for i in self.items): raise ValueError("插件不存在")
        if not isinstance(enabled, bool): raise ValueError("enabled 必须是布尔值")
        previous = dict(self.config.plugin_flags)
        self.config.plugin_flags[name] = enabled
        try: self.config.save()
        except Exception:
            self.config.plugin_flags = previous
            raise
        if not enabled: await self._kill(name)
        self.errors.pop(name, None)

    def match(self, text):
        command = text.split(maxsplit=1)[0] if text.strip() else ""
        prefix = getattr(self.config, 'data', {}).get('command_prefix', '/')
        if prefix != '/': command = '/' + command[len(prefix):] if command.startswith(prefix) else ''
        image_command = re.fullmatch(r'(/偷表情(?:包)?)(?:\[(?:图片|表情)\])+', command)
        if image_command: command = image_command[1]
        aliases = {"/": "/帮助", "/help": "/帮助", "/reset": "/新对话"}
        command = aliases.get(command, command)
        for item in self.items:
            if command in item["commands"]: return item, command
        explicit_music = music_intent(text)
        if explicit_music:
            item=next((i for i in self.items if i['id']=='music_search' and self.enabled(i['id'])),None)
            if item:return item,'@music'
        # Exact host matching only; URL text cannot supply a regex, credentials or port.
        links = []
        for value in re.findall(r"""https?://[^\s<>\[\]()"']+""", text[:4000], re.I):
            try:
                location = urlsplit(value.rstrip(".,，。!！?？;；)]}>\"'"))
                if location.username or location.password or location.port is not None: continue
                if location.hostname: links.append((location.hostname.lower(), location.path))
            except ValueError:
                continue
        for item in self.items:
            if (self.enabled(item["id"]) and self.settings(item['id']).get('auto_parse', True) and any(host in item.get("auto_url_hosts", [])
                    and path.startswith(item.get("auto_url_paths", {}).get(host, "/")) for host, path in links)):
                return item, "@url"
        return None, command

    def get_settings(self, name):
        item = next((i for i in self.items if i['id'] == name), None)
        if item is None: raise ValueError('插件不存在')
        fields = [dict(f) for f in BUILTIN_FIELDS.get(name, [])]
        fields.extend(dict(f) for f in OUTPUT_FIELDS)
        if 'worker' in item: fields.extend(dict(f) for f in COMMON_FIELDS)
        if item.get('game_ai_adapter')==1:fields.extend(dict(f) for f in GAME_FIELDS)
        if item.get('auto_url_hosts'):
            fields.append(dict(key='auto_parse', type='boolean', label='自动解析聊天中的匹配链接', default=True))
        for field in item.get('config_schema', []):
            if (isinstance(field, dict) and re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_]{0,39}', str(field.get('key','')))
                    and field.get('type') in ('string','integer','number','boolean')
                    and not any(f['key'] == field['key'] for f in fields)):
                fields.append({k:v for k,v in field.items() if k in ('key','type','label','min','max','default','max_length','description','options')})
        values = {f['key']: f.get('default', '' if f['type']=='string' else False if f['type']=='boolean' else 1) for f in fields}
        values.update(getattr(self.config, 'plugin_settings', {}).get(name, {}))
        return {'id':name, 'schema':{'fields': fields}, 'values':values, 'applies_to':'next_invocation'}

    def settings(self, name): return self.get_settings(name)['values']

    def validate_settings(self, name, values):
        fields = {f['key']: f for f in self.get_settings(name)['schema']['fields']}
        if not isinstance(values, dict) or set(values)-set(fields): raise ValueError('插件参数字段无效')
        clean = {}
        for key, value in values.items():
            f = fields[key]
            if f['type']=='boolean': valid=isinstance(value, bool)
            elif f['type']=='string': valid=isinstance(value, str) and len(value)<=f.get('max_length', 2000)
            else:
                valid=(isinstance(value, (int,float)) and not isinstance(value,bool)
                    and (f['type']!='integer' or isinstance(value,int)) and f.get('min',-1000000)<=value<=f.get('max',1000000))
            if not valid: raise ValueError(str(f.get('label',key))+'取值无效')
            if isinstance(f.get('options'),list) and value not in f['options']:
                raise ValueError(str(f.get('label',key))+'不在允许选项中')
            if name in NOTICE_FIELDS:
                validate_notice_setting(key, value)
            if name=='group_admin' and key=='operators' and any(not re.fullmatch(r'[1-9][0-9]{0,19}',token) for token in re.split(r'[\s,，]+',value.strip()) if token):
                raise ValueError('群管QQ须为数字，每行一个。')
            if name=='keyword_reply' and key=='rules_json':
                try:
                    rules=json.loads(value)
                    if not isinstance(rules,list) or len(rules)>50:raise ValueError()
                    for rule in rules:
                        if (not isinstance(rule,dict) or set(rule)-{'key','reply','mode','media'} or not isinstance(rule.get('key'),str)
                            or not 1<=len(rule['key'])<=80 or any(ord(c)<32 for c in rule['key'])
                            or not isinstance(rule.get('reply'),str) or not 1<=len(rule['reply'])<=800
                            or rule.get('mode','exact') not in ('exact','contains')):raise ValueError()
                        if 'media' in rule:validate_result({'version':1,'text':rule['reply'],'media':rule['media']})
                except (ValueError,TypeError):raise ValueError('规则须为最多50条含key/reply/mode的JSON数组。') from None
            clean[key]=value
        return clean

    def update_settings(self, name, values):
        if self.mutating: raise ValueError('插件配置正在更新，请稍后修改')
        clean=self.validate_settings(name,values)
        if not hasattr(self.config,'plugin_settings'): self.config.plugin_settings={}
        previous = copy.deepcopy(self.config.plugin_settings)
        self.config.plugin_settings.setdefault(name,{}).update(clean)
        try: self.config.save()
        except Exception:
            self.config.plugin_settings = previous
            raise
        return self.get_settings(name)

    async def _kill(self, name):
        proc = self.workers.pop(name, None)
        self.worker_used.pop(name,None)
        if proc and proc.returncode is None:
            proc.kill()
            await proc.wait()

    def _state_directory(self, name):
        # Stable instance data is separate from versioned package directories.
        # A legacy hand-written manifest must not turn its id into a path.
        if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,39}', name):
            raise ValueError('invalid plugin state id')
        def linked(path):
            return path.is_symlink() or (getattr(path, 'is_junction', lambda: False)())
        if linked(self.root): raise ValueError('linked plugin root')
        self.root.mkdir(parents=True, exist_ok=True)
        root = self.root.resolve()
        state = self.root / 'state'
        if linked(state): raise ValueError('linked plugin state root')
        state.mkdir(mode=0o700, exist_ok=True)
        if state.resolve() != root / 'state': raise ValueError('plugin state root escaped')
        state.chmod(0o700)
        target = state / name
        if linked(target): raise ValueError('linked plugin state directory')
        target.mkdir(mode=0o700, exist_ok=True)
        if target.resolve().parent != state.resolve(): raise ValueError('plugin state directory escaped')
        target.chmod(0o700)
        return target.resolve()

    async def execute(self, item, text, context, timeout=None):
        if self.mutating: return '插件配置正在更新，请稍后调用。'
        values=self.settings(item['id'])
        configured_timeout=values.get('timeout',2.0)
        timeout=configured_timeout if timeout is None else min(timeout,configured_timeout)
        text=text[:min(values.get('input_max_chars',4000),getattr(self.config,'data',{}).get('command_max_chars',4000))]
        host_fields={field['key'] for field in OUTPUT_FIELDS}
        worker_values={key:value for key,value in values.items() if key not in host_fields}
        context={**context,'settings':worker_values, 'self_id':getattr(self.config,'self_id',''),
                 'owner_ids':getattr(self.config,'owner_ids',[getattr(self.config,'owner_id','')]),'rich_result_version':1}
        if item.get('game_ai_adapter')==1 and item['id'] in GAMES:
            context['_game_ai']={**context.get('_game_ai',{}),'pid':item['id'],
                'bot_name':getattr(self.config,'data',{}).get('name','机器人'),
                'enabled':getattr(self.config,'data',{}).get('game_ai_enabled',True)}
            if not context['_game_ai']['enabled']:context['settings']={**worker_values,'ai_enabled':False,'ai_auto_join':False}
        else:context.pop('_game_ai',None)
        if item['id']=='music_search':text=music_intent(text) or text
        if 'worker' in item:
            try:
                # Never accept a state path from a caller or a chat message.
                context['data_dir'] = str(self._state_directory(item['id']))
            except (OSError, ValueError):
                self.errors[item['id']] = '插件数据目录无效或不可写'
                return '这个插件的数据目录暂时不可用，其他功能仍可使用。'
        try:
            async with asyncio.timeout(timeout):
                return await self._execute(item, text, context, timeout)
        except TimeoutError:
            self.errors[item["id"]] = "插件执行超时"
            return "这个插件暂时没响应，其他功能仍可使用。"

    async def _execute(self, item, text, context, timeout):
        name = item["id"]
        lock = self.locks.setdefault(name, asyncio.Lock())
        async with lock:
            try:
                starting=False
                async with self.pool_lock:
                    maximum=getattr(self.config,'data',{}).get('plugin_worker_limit',8)
                    while len(self.workers)>maximum:
                        idle=[pid for pid in self.workers if pid!=name and not self.locks.get(pid,asyncio.Lock()).locked()]
                        if not idle:break
                        await self._kill(min(idle,key=lambda pid:self.worker_used.get(pid,0)))
                    proc = self.workers.get(name)
                    if not proc or proc.returncode is not None:
                        if proc:await self._kill(name)
                        while len(self.workers)>=maximum:
                            idle=[pid for pid in self.workers if pid!=name and not self.locks.get(pid,asyncio.Lock()).locked()]
                            if not idle:return '插件进程都在处理任务，请稍后再试。'
                            await self._kill(min(idle,key=lambda pid:self.worker_used.get(pid,0)))
                        path = (self.root / item["worker"]).resolve()
                        if not path.is_relative_to(self.root.resolve()): raise ValueError("path")
                        proc = await asyncio.create_subprocess_exec(sys.executable, "-X", "utf8", "-m", "yukino_core.plugin_worker", str(path),
                            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                            limit=131072)
                        self.workers[name] = proc
                        starting=True
                    self.worker_used[name]=time.monotonic()
                # A plugin stuck while importing must not hold the global pool
                # lock and delay unrelated cold starts. The per-plugin lock
                # still owns this handshake and prevents concurrent readers.
                if starting:
                    ready = json.loads(await asyncio.wait_for(proc.stdout.readline(), timeout))
                    if ready.get("ready") is not True: raise ValueError("not ready")
                proc.stdin.write((json.dumps({"text": text, "context": context}, ensure_ascii=False) + "\n").encode())
                await proc.stdin.drain()
                result = json.loads(await asyncio.wait_for(proc.stdout.readline(), timeout))
                if "error" in result: raise ValueError("plugin failed")
                value = result.get("text")
                value=validate_result(value,self.settings(name).get('output_max_chars',4000))
                self.errors.pop(name, None)
                self.worker_used[name]=time.monotonic()
                return value
            except (Exception, asyncio.CancelledError) as e:
                await self._kill(name)
                self.errors[name] = "插件执行失败或超时"
                if isinstance(e, asyncio.CancelledError): raise
                return "这个插件暂时没响应，其他功能仍可使用。"

    async def close(self):
        for name in list(self.workers): await self._kill(name)
