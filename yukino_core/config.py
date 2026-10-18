from __future__ import annotations

import json
import os
import copy
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

PERSONA = "你是Yukino，一个友善、简洁的QQ聊天助手。认真回答问题，不编造经历、能力或操作结果。"

DEFAULTS = dict(name="Yukino", persona="你是Yukino，一个友善、简洁的QQ聊天助手。认真回答问题，不编造经历、能力或操作结果。", private_enabled=True, group_enabled=True,
                group_allowlist=[], trigger_names=["Yukino"], history_limit=14,
                max_pending=24, merge_window_ms=700, response_max_chars=1800,
                provider_timeout=100, mention_enabled=True, reply_trigger_enabled=True,
                trigger_prefix_enabled=True, group_reply_all=False, ignore_bots=True,
                memory_enabled=True, resource_lookup_enabled=True, emoji_enabled=True,
                private_allowlist=[], user_blocklist=[], group_blocklist=[],
                history_char_limit=32000, current_message_max_chars=6000, memory_limit=8,
                resource_limit=2, emoji_max_per_reply=1, session_cooldown_ms=0,
                session_max_pending=20, command_max_chars=4000, health_interval=20,
                command_prefix="/", web_prompt_marker="yukino-brat-20261014", plugin_worker_limit=8)

BOOLEANS = {key for key, value in DEFAULTS.items() if isinstance(value, bool)}
LISTS = {key for key, value in DEFAULTS.items() if isinstance(value, list)}
LIMITS = {"history_limit": (0, 100), "max_pending": (1, 500), "merge_window_ms": (0, 10000),
          "response_max_chars": (100, 16000), "provider_timeout": (5, 600),
          "history_char_limit": (1000, 128000), "current_message_max_chars": (100, 32000),
          "memory_limit": (0, 30), "resource_limit": (0, 12), "emoji_max_per_reply": (0, 4),
          "session_cooldown_ms": (0, 60000), "session_max_pending": (1, 100),
          "command_max_chars": (100, 8000), "health_interval": (5, 300), "plugin_worker_limit": (1,24)}

LABELS = {
 "name":("general","实例名称"), "persona":("persona","系统人格"),
 "private_enabled":("access","启用私聊"), "group_enabled":("access","启用群聊"),
 "group_allowlist":("access","群聊白名单"), "private_allowlist":("access","私聊白名单"),
 "user_blocklist":("access","用户黑名单"), "group_blocklist":("access","群聊黑名单"),
 "ignore_bots":("access","忽略被平台标记为机器人的消息"),
 "trigger_names":("triggers","名字触发词"), "mention_enabled":("triggers","群聊被 @ 时回复"),
 "reply_trigger_enabled":("triggers","回复机器人消息时触发"),
 "trigger_prefix_enabled":("triggers","名字开头时触发"), "group_reply_all":("triggers","群聊所有消息触发"),
 "command_prefix":("triggers","指令前缀"), "history_limit":("context","上下文消息条数"),
 "history_char_limit":("context","历史总字符上限"), "current_message_max_chars":("context","本轮输入字符上限"),
 "memory_enabled":("context","向模型提供长期记忆"), "memory_limit":("context","每轮记忆条数"),
 "resource_lookup_enabled":("context","向模型提供本地知识"), "resource_limit":("context","每类知识检索条数"),
 "emoji_enabled":("context","允许发送表情图片"), "emoji_max_per_reply":("context","每条模型回复图片上限"),
 "response_max_chars":("context","回复字符上限"), "web_prompt_marker":("persona","网页自定义指令标记"),
 "max_pending":("runtime","全局待处理上限"), "merge_window_ms":("runtime","连续消息合并窗口（毫秒）"),
 "session_cooldown_ms":("runtime","会话回复冷却（毫秒）"), "session_max_pending":("runtime","单会话待处理上限"),
 "provider_timeout":("runtime","模型请求总超时（秒）"), "command_max_chars":("runtime","插件输入字符上限"), "plugin_worker_limit":("runtime","插件进程数量上限"),
 "health_interval":("runtime","连接检查间隔（秒）")}


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.'+path.name+'.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def settings_schema():
    return {"version": 1, "fields": [dict(key=key, label=LABELS[key][1], section=LABELS[key][0],
        type="boolean" if key in BOOLEANS else "list" if key in LISTS else "integer" if key in LIMITS else "text" if key == "persona" else "string",
        default=copy.deepcopy(value), restart_required=False,
        description=("留空表示不限制；黑名单优先。每行一个数字ID。" if key in LISTS and key != "trigger_names" else
                     "仅当上游平台提供机器人标记时识别；不会猜测普通QQ用户身份。" if key == "ignore_bots" else
                     "仅在请求提示中加入标记；留空关闭，不会自动修改网页账号记忆。" if key == "web_prompt_marker" else
                     "保存后用于新收到的消息；正在生成的回复继续使用已提交的请求。"),
        **({"min": LIMITS[key][0], "max": LIMITS[key][1]} if key in LIMITS else {}))
        for key, value in DEFAULTS.items()]}


class Config:
    def __init__(self, path):
        self.path = Path(path)
        self.data = copy.deepcopy(DEFAULTS)
        self.plugin_flags = {}
        self.plugin_settings = {}
        self.session_providers = {}
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.data.update(self.validate({k: v for k, v in raw.items() if k in DEFAULTS}))
            self.plugin_flags = {str(k): bool(v) for k, v in raw.get("plugins", {}).items()}
            self.plugin_settings = raw.get("plugin_settings", {}) if isinstance(raw.get("plugin_settings"), dict) else {}
            self.session_providers = {k: v for k, v in raw.get("session_providers", {}).items()
                                      if isinstance(k, str) and re.fullmatch(r"[pg]:\d{1,30}", k) and isinstance(v, str)}
        self.admin_token = os.environ.get("YUKINO_ADMIN_TOKEN", "")
        self.onebot_token = os.environ.get("YUKINO_ONEBOT_TOKEN", "")
        self.provider_token = os.environ.get("YUKINO_PROVIDER_TOKEN", "")
        secrets_path = os.environ.get("YUKINO_SECRETS_FILE")
        if secrets_path:
            secrets = json.loads(Path(secrets_path).read_text(encoding="utf-8"))
            self.admin_token = secrets["admin_token"]
            self.onebot_token = secrets["onebot_token"]
            self.provider_token = secrets["provider_token"]
        if len(self.admin_token) < 16:
            raise ValueError("管理令牌至少需要16个字符；请设置 YUKINO_ADMIN_TOKEN 或私密配置文件")
        self.onebot_url = os.environ.get("YUKINO_ONEBOT_URL", "ws://napcat:3001")
        self.provider_url = os.environ.get("YUKINO_PROVIDER_URL", "http://chatgpt-web:17656")
        self.self_id = os.environ.get("YUKINO_SELF_ID", "")
        self.owner_id = os.environ.get("YUKINO_OWNER_ID", "")
        self.receive_enabled = os.environ.get("YUKINO_RECEIVE_ENABLED", "true").lower() == "true"
        self.connection_path = self.path.with_name("connections.json")
        self.connection_data = dict(onebot_url=self.onebot_url, self_id=self.self_id,
            owner_ids=[self.owner_id] if self.owner_id else [], receive_enabled=self.receive_enabled,
            action_timeout=15, reconnect_min=0.5, reconnect_max=30, onebot_token=self.onebot_token)
        if self.connection_path.exists():
            self.connection_data.update(self.validate_connections(json.loads(self.connection_path.read_text(encoding="utf-8"))))
        self._apply_connections()

    @staticmethod
    def validate(patch):
        if not isinstance(patch, dict) or any(k not in DEFAULTS for k in patch):
            raise ValueError("配置字段无效")
        out = {}
        for k, v in patch.items():
            if k in BOOLEANS:
                if not isinstance(v, bool): raise ValueError(k + " 必须是布尔值")
            elif k in ("name", "persona", "web_prompt_marker", "command_prefix"):
                maximum = 32000 if k == "persona" else 100 if k == "web_prompt_marker" else 40 if k == "name" else 8
                if not isinstance(v, str) or (k != "web_prompt_marker" and not v.strip()) or len(v) > maximum:
                    raise ValueError(k + " 长度无效")
                v = v.strip()
                if k == "command_prefix" and (any(c.isspace() for c in v) or any(c.isalnum() for c in v)):
                    raise ValueError("指令前缀请使用1—8个非空白标点")
                if k == "web_prompt_marker" and any(c in v for c in "\r\n"):
                    raise ValueError("网页标记不能包含换行")
            elif k in LISTS:
                if not isinstance(v, list) or len(v) > 200 or any(not isinstance(i, str) or len(i) > 80 for i in v):
                    raise ValueError(k + " 必须是字符串列表")
                if k != "trigger_names" and any(not i.strip().isdigit() for i in v if i.strip()):
                    raise ValueError("账号及群号必须为数字字符串")
                v = list(dict.fromkeys(i.strip() for i in v if i.strip()))
            else:
                if not isinstance(v, int) or isinstance(v, bool) or not LIMITS[k][0] <= v <= LIMITS[k][1]:
                    raise ValueError(k + " 超出范围")
            out[k] = v
        return out

    def save(self):
        atomic_json(self.path, {**self.data, "plugins": self.plugin_flags, "plugin_settings": self.plugin_settings,
                               "session_providers": self.session_providers})

    def update(self, patch):
        previous = self.data
        self.data = {**previous, **self.validate(patch)}
        try: self.save()
        except Exception:
            self.data = previous
            raise
        return dict(self.data)

    @staticmethod
    def validate_connections(patch):
        if not isinstance(patch, dict) or set(patch) - {"onebot_url", "self_id", "owner_ids", "receive_enabled", "onebot_token", "clear_token", "action_timeout", "reconnect_min", "reconnect_max"}:
            raise ValueError("连接字段无效")
        out = {}
        for key, value in patch.items():
            if key in ("receive_enabled", "clear_token"):
                if not isinstance(value, bool): raise ValueError(key + "必须为布尔值")
            elif key == "onebot_url":
                if not isinstance(value, str) or len(value) > 2048: raise ValueError("连接地址无效")
                parsed = urlsplit(value)
                if parsed.scheme not in ("ws", "wss") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                    raise ValueError("请输入不含凭据和查询参数的 ws:// 或 wss:// 地址")
                parsed.port
            elif key in ("self_id", "onebot_token"):
                if not isinstance(value, str) or len(value) > (30 if key == "self_id" else 4096) or any(c in value for c in "\r\n"):
                    raise ValueError(key + "无效")
                if key == "self_id" and value and not value.isdigit(): raise ValueError("机器人QQ号须为数字")
            elif key == "owner_ids":
                if not isinstance(value, list) or len(value) > 100 or any(not isinstance(x, str) or not x.isdigit() or len(x)>30 for x in value):
                    raise ValueError("主人QQ须为数字字符串列表")
                value = list(dict.fromkeys(value))
            else:
                bounds = {"action_timeout": (1, 600), "reconnect_min": (0.1, 60), "reconnect_max": (1, 600)}
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not bounds[key][0] <= value <= bounds[key][1]:
                    raise ValueError(key + "超出范围")
            out[key] = value
        return out

    def _apply_connections(self):
        for key in ("onebot_url", "self_id", "receive_enabled", "onebot_token"):
            setattr(self, key, self.connection_data[key])
        self.owner_ids = self.connection_data["owner_ids"]
        self.owner_id = self.owner_ids[0] if self.owner_ids else ""

    def connections(self):
        return {**{k:v for k,v in self.connection_data.items() if k != "onebot_token"},
                "token_configured": bool(self.onebot_token)}

    def update_connections(self, patch):
        clean = self.validate_connections(patch)
        merged = {**self.connection_data, **{k:v for k,v in clean.items() if k not in ("onebot_token", "clear_token")}}
        if clean.get("onebot_token"): merged["onebot_token"] = clean["onebot_token"]
        if clean.get("clear_token"): merged["onebot_token"] = ""
        if merged["reconnect_max"] < merged["reconnect_min"]: raise ValueError("重连最大间隔不能小于最小间隔")
        atomic_json(self.connection_path, merged)
        self.connection_data = merged
        self._apply_connections()
        return self.connections()
