"""Local, opt-in OneBot notice interactions; no model calls or message history."""
from __future__ import annotations

import random
import re
import string

from .transport import OneBotError


NOTICE_PLUGINS = [
    dict(id="poke", name="戳一戳互动", description="私聊/群聊戳机器人立即本地回应；可附真实表情及回戳，不经过模型", commands=[], default_enabled=True),
    dict(id="group_welcome", name="入群欢迎", description="收到真实入群事件后欢迎新成员，支持群范围与模板", commands=[], default_enabled=False),
    dict(id="group_farewell", name="退群提示", description="区分主动退群与被移出，机器人自身退群不发送", commands=[], default_enabled=False),
    dict(id="group_admin_notice", name="管理员变更提示", description="收到实际管理员任免事件后在本群提示", commands=[], default_enabled=False),
    dict(id="group_title_notice", name="群头衔提示", description="收到实际群头衔变更事件后在本群提示", commands=[], default_enabled=False),
    dict(id="group_card_notice", name="群名片变更提示", description="显示真实群名片前后变化，默认关闭避免打扰", commands=[], default_enabled=False),
]

_TEMPLATES = {
    "poke": "戳什么戳，我在呢。\n又戳我？有事快说。\n哼，收到了，别戳啦。",
    "group_welcome": "欢迎 {user} 加入！有问题可以在群里问。",
    "group_farewell": "{user} {action}。",
    "group_admin_notice": "{user} {action}。",
    "group_title_notice": "{user} 的群头衔更新为「{title}」。",
    "group_card_notice": "{user} 的群名片从「{old_card}」改为「{new_card}」。",
}
TEMPLATE_FIELDS = {"user", "user_id", "group_id", "bot", "action", "title", "old_card", "new_card"}


def notice_fields(pid):
    fields = [
        dict(key="templates", type="string", label="回复模板（每行一条，随机选用）", max_length=6000,
             default=_TEMPLATES[pid], description="支持 {user}、{user_id}、{group_id}、{bot}、{action}、{title}、{old_card}、{new_card}；每条最多500字。"),
        dict(key="probability", type="number", label="响应概率（0到1）", min=0, max=1, default=1),
        dict(key="session_cooldown_seconds", type="integer", label="同会话回复冷却秒数（重启保留）", min=0, max=3600, default=5),
        dict(key="user_cooldown_seconds", type="integer", label="同会话同用户冷却秒数（重启保留）", min=0, max=86400, default=15),
        dict(key="group_allowlist", type="string", label="仅指定群生效（每行一个群号，空为全部允许的群）", max_length=6000, default=""),
        dict(key="mention_user", type="boolean", label="群内回复时@触发者", default=pid == "group_welcome"),
    ]
    if pid == "poke":
        fields += [
            dict(key="in_private", type="boolean", label="私聊戳一戳", default=True),
            dict(key="in_groups", type="boolean", label="群聊戳一戳", default=True),
            dict(key="attach_emoji", type="boolean", label="附带一张资源库真实表情（无可用图时仅发文字）", default=False),
            dict(key="emoji_query", type="string", label="表情检索词（空为随机）", max_length=80, default=""),
            dict(key="poke_back", type="boolean", label="成功回复后回戳一次（默认关闭）", default=False),
        ]
    return fields


NOTICE_FIELDS = {item["id"]: notice_fields(item["id"]) for item in NOTICE_PLUGINS}


def validate_notice_setting(key, value):
    if key == "group_allowlist":
        tokens = re.split(r"[\s,，]+", value.strip())
        if len(tokens) > 200 or any(not re.fullmatch(r"[1-9][0-9]{0,19}", token) for token in tokens if token):
            raise ValueError("群范围须为最多200个有效数字群号。")
    if key == "templates":
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        if not lines or len(lines) > 30 or any(len(line) > 500 for line in lines):
            raise ValueError("请填写1—30条回复模板，每条最多500字。")
        try:
            for line in lines:
                for _, field, spec, conversion in string.Formatter().parse(line):
                    if field is not None and (field not in TEMPLATE_FIELDS or spec or conversion):
                        raise ValueError()
        except ValueError:
            raise ValueError("模板只支持页面列出的占位符，不支持属性、格式表达式；字面花括号请写 {{ 和 }}。") from None


class NoticeExtensions:
    def __init__(self, kernel):
        self.k = kernel
        self.rng = random.SystemRandom()

    @staticmethod
    def plugin_id(event):
        kind, sub = event.get("notice_type"), event.get("sub_type")
        if kind == "notify":
            return {"poke": "poke", "title": "group_title_notice"}.get(sub)
        return {"group_increase": "group_welcome", "group_decrease": "group_farewell",
                "group_admin": "group_admin_notice", "group_card": "group_card_notice"}.get(kind)

    def permitted(self, event, settings):
        cfg = self.k.config.data
        own = str(self.k.config.self_id or getattr(self.k.transport, "self_id", "") or event.get("self_id", ""))
        if (not own or event.get("self_id") != own or event["user_id"] == own
                or event["user_id"] in cfg["user_blocklist"] or (cfg["ignore_bots"] and event.get("is_bot"))):
            return False
        if event.get("sub_type") == "poke" and event.get("target_id") != own:
            return False
        if event["message_type"] == "private":
            return (cfg["private_enabled"] and settings.get("in_private", True)
                    and (not cfg["private_allowlist"] or event["user_id"] in cfg["private_allowlist"]))
        groups = {x for x in re.split(r"[\s,，]+", settings["group_allowlist"].strip()) if x}
        return (cfg["group_enabled"] and settings.get("in_groups", True)
                and event["group_id"] not in cfg["group_blocklist"]
                and (not cfg["group_allowlist"] or event["group_id"] in cfg["group_allowlist"])
                and (not groups or event["group_id"] in groups))

    async def handle(self, event):
        pid = self.plugin_id(event)
        if pid is None or not self.k.plugins.enabled(pid):
            await self.k.store.mark_event(event["event_id"], "notice_disabled")
            return
        settings = self.k.plugins.settings(pid)
        if not self.permitted(event, settings):
            self.k.metrics["ignored"] += 1
            await self.k.store.mark_event(event["event_id"], "notice_ignored")
            return
        if self.rng.random() >= settings["probability"]:
            await self.k.store.mark_event(event["event_id"], "notice_probability")
            return
        sid, uid = event["session_id"], event["user_id"]
        allowed = await self.k.store.claim_notice_cooldown({
            f"notice:{pid}:{sid}": settings["session_cooldown_seconds"],
            f"notice:{pid}:{sid}:{uid}": settings["user_cooldown_seconds"],
        })
        if not allowed:
            await self.k.store.mark_event(event["event_id"], "notice_cooldown")
            return
        action = {"leave": "退出了群聊", "kick": "被移出了群聊", "set": "成为了管理员", "unset": "不再担任管理员"}.get(event["sub_type"], "触发了互动")
        fields = dict(user=uid, user_id=uid, group_id=event["group_id"], bot=self.k.config.data["name"],
                      action=action, title=event.get("title", "") or "（空）",
                      old_card=event.get("card_old", "") or "（空）", new_card=event.get("card_new", "") or "（空）")
        lines = [line.strip() for line in settings["templates"].splitlines() if line.strip()]
        text = self.rng.choice(lines).format_map(fields)[:self.k.config.data["response_max_chars"]]
        segments = [{"type": "text", "data": {"text": text}}]
        if settings["mention_user"] and event["message_type"] == "group":
            segments = [{"type": "at", "data": {"qq": uid}}, {"type": "text", "data": {"text": " "}}] + segments
        attachments = []
        if (pid == "poke" and settings["attach_emoji"] and self.k.config.data["emoji_enabled"]
                and self.k.config.data["emoji_max_per_reply"] > 0 and self.k.plugins.enabled("emoji")):
            image = await self.k._emoji_attachment(settings["emoji_query"])
            if image:
                segments.append(image[0]); attachments.append(image[1])
        # A pending pause wins over an image read or database operation.
        if self.k.closing or not getattr(self.k.config, "receive_enabled", True):
            await self.k.store.mark_event(event["event_id"], "receive_disabled")
            return
        sent = await self.k.reply(event, text, segments=segments, attachments=attachments)
        if not sent:
            return
        self.k.metrics["notice_replied"] += 1
        if pid != "poke" or not settings["poke_back"]:
            return
        if self.k.closing or not getattr(self.k.config, "receive_enabled", True):
            return
        params = {"user_id": uid}
        if event["message_type"] == "group":
            params["group_id"] = event["group_id"]
        # The incoming receipt is already durably consumed. Never retry a
        # back-poke after rejection, timeout, reconnect or process restart.
        await self.k.store.mark_event(event["event_id"], "notice_backpoke_sending")
        try:
            await self.k.transport.call("send_poke", params, timeout=5)
            await self.k.store.mark_event(event["event_id"], "notice_replied_poked")
        except OneBotError as error:
            state = "notice_backpoke_unknown" if error.uncertain else "notice_backpoke_failed"
            await self.k.store.mark_event(event["event_id"], state, "文字回复成功；回戳未确认，不会重试")
        except Exception:
            await self.k.store.mark_event(event["event_id"], "notice_backpoke_unknown", "文字回复成功；回戳结果未知，不会重试")
