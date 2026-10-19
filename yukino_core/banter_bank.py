"""Bounded original interaction copy and non-destructive config merging."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import string

MAX_POKE_LINES = 100
MAX_TEMPLATE_CHARS = 6000
CATEGORIES = ("招呼", "闲聊", "无聊", "夸夸", "安慰", "嘴硬关心")
FIELDS = {"user", "user_id", "group_id", "bot", "action", "title", "old_card", "new_card"}
KEYWORDS = (("在吗", "招呼"), ("有人吗", "招呼"), ("说句话", "闲聊"), ("唠两句", "闲聊"),
            ("好无聊", "无聊"), ("找点话题", "无聊"), ("夸夸我", "夸夸"), ("夸我一下", "夸夸"),
            ("安慰一下", "安慰"), ("有点难过", "安慰"), ("关心我一下", "嘴硬关心"), ("嘴硬一下", "嘴硬关心"))


def load_bank(path: Path | str | None = None) -> dict:
    source = Path(path) if path is not None else Path(__file__).resolve().parent.parent / "resources/banter.zh-CN.json"
    if source.is_symlink() or not source.is_file() or source.stat().st_size > 65536:
        raise ValueError("文案资源须为不超过64KiB的普通JSON文件。")
    bank = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(bank, dict) or bank.get("format") != 1 or bank.get("locale") != "zh-CN":
        raise ValueError("不支持的文案资源格式。")
    talk = bank.get("smalltalk")
    if not isinstance(talk, dict) or set(talk) != set(CATEGORIES):
        raise ValueError("文案资源须包含全部六类搭话。")
    for key, rows in [("poke", bank.get("poke")), *talk.items()]:
        if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
            raise ValueError("每类文案须有1—100条。")
        for row in rows:
            if not isinstance(row, str) or not 1 <= len(row.strip()) <= 80 or any(ord(c) < 32 for c in row):
                raise ValueError("文案须为不超过80字的单行文字。")
            for _, field, spec, conversion in string.Formatter().parse(row):
                if field is not None and (key != "poke" or field not in FIELDS or spec or conversion):
                    raise ValueError("文案包含不支持的占位符。")
        if len(rows) != len(set(rows)):
            raise ValueError("同类文案存在重复。")
    return bank


def merge_banter(config: dict, bank: dict, *, include_keywords: bool = False) -> tuple[dict, dict]:
    """Return a changed copy only; reject the whole merge rather than truncate."""
    result = copy.deepcopy(config)
    settings = result.setdefault("plugin_settings", {})
    poke = settings.setdefault("poke", {})
    original = poke.get("templates", "")
    if not isinstance(original, str):
        raise ValueError("现有戳一戳模板格式无效，未修改配置。")
    lines = list(dict.fromkeys(row.strip() for row in original.splitlines() if row.strip()))
    before = len(lines)
    for row in bank["poke"]:
        if row not in lines:
            lines.append(row)
    text = "\n".join(lines)
    if len(lines) > MAX_POKE_LINES or len(text) > MAX_TEMPLATE_CHARS:
        raise ValueError("合并后超过100条或6000字符，未截断、未修改配置。")
    poke["templates"] = text
    added = total = 0
    if include_keywords:
        keyword = settings.setdefault("keyword_reply", {})
        rows = json.loads(keyword.get("rules_json", "[]"))
        if not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get("key"), str)
                                           or not isinstance(row.get("reply"), str) for row in rows):
            raise ValueError("现有关键词规则格式无效，未修改配置。")
        for index, (key, category) in enumerate(KEYWORDS):
            if any(row["key"] == key for row in rows):
                continue
            choices = bank["smalltalk"][category]
            rows.append({"key": key, "reply": choices[index % len(choices)], "mode": "exact"})
            added += 1
        encoded = json.dumps(rows, ensure_ascii=False)
        if len(rows) > 50 or len(encoded) > 60000:
            raise ValueError("合并后关键词超过50条或60000字符，未截断、未修改配置。")
        keyword["rules_json"] = encoded
        total = len(rows)
    return result, {"poke_added": len(lines) - before, "poke_total": len(lines), "poke_chars": len(text),
                    "keyword_added": added, "keyword_total": total}
