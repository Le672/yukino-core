"""Model profiles, private credentials and non-streaming HTTP adapters.

Only the administrator supplies provider URLs. API keys are write-only at the
public boundary; provider errors never include response bodies or request URLs.
Generation is deliberately attempted once, without fallbacks or paid retries.
"""
from __future__ import annotations

import asyncio
import copy
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import quote, urlsplit
import uuid

import aiohttp

from .provider import ProviderError


PROVIDER_TYPES = {
    "chatgpt_web": {"name": "ChatGPT 网页桥接", "base_url": "http://chatgpt-web:17656", "model": "chatgpt-web"},
    "openai_compatible": {"name": "OpenAI 兼容 Chat Completions", "base_url": "https://api.openai.com/v1", "model": ""},
    "openai_responses": {"name": "OpenAI Responses", "base_url": "https://api.openai.com/v1", "model": ""},
    "anthropic": {"name": "Anthropic Messages", "base_url": "https://api.anthropic.com/v1", "model": ""},
    "gemini": {"name": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta", "model": ""},
    "ollama": {"name": "Ollama", "base_url": "http://127.0.0.1:11434", "model": ""},
}
_FIELDS = {"id", "name", "type", "base_url", "model", "enabled", "api_key", "clear_api_key",
           "temperature", "top_p", "max_tokens", "timeout", "extra_body"}
_READ_ONLY = {"has_api_key", "is_default", "health", "configured"}
_RESERVED = {"model", "stream", "messages", "input", "contents", "yukino_raw_prompt"}
_CREDENTIAL_FIELDS = {"apikey", "authorization", "authentication", "auth", "bearer", "headers", "token", "accesstoken", "refreshtoken", "secret", "password", "credential", "credentials"}
_WEB_ERRORS = {
    "chatgpt_login_required": "网页需要重新登录或验证，去服务器桌面处理一下吧",
    "chatgpt_confirmation_required": "网页有待处理的弹窗，去服务器桌面看一下吧",
    "queue_full": "网页请求队列已满，稍后再试吧",
    "chatgpt_page_error": "网页提示额度或请求错误，稍后再试吧",
}
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def _number(value, low, high, field, *, integer=False, nullable=True):
    if value is None and nullable:
        return None
    valid = isinstance(value, int) if integer else isinstance(value, (int, float))
    if not valid or isinstance(value, bool) or not low <= value <= high or not math.isfinite(value):
        raise ValueError(field + " 超出范围")
    return int(value) if integer else value


def _safe_url(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise ValueError("服务地址无效")
    value = value.strip().rstrip("/")
    if any(ord(c) < 33 or ord(c) == 127 for c in value) or "\\" in value:
        raise ValueError("服务地址不能包含空格或控制字符")
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError
        if parsed.query or parsed.fragment or "?" in value or "#" in value:
            raise ValueError
        parsed.port
    except ValueError:
        raise ValueError("服务地址须为 HTTP(S)，不能内嵌账号、密钥、查询参数或片段") from None
    return value


def _validate_extra(value):
    if not isinstance(value, dict):
        raise ValueError("extra_body 必须为 JSON 对象")
    try:
        serialized = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise ValueError("extra_body 必须是有效的 JSON 对象") from None
    if len(serialized) > 16384:
        raise ValueError("extra_body 长度超出范围")

    def visit(obj, depth=0):
        if depth > 12:
            raise ValueError("extra_body 嵌套过深")
        if isinstance(obj, dict):
            for key, child in obj.items():
                if not isinstance(key, str):
                    raise ValueError("extra_body 的字段名必须为字符串")
                normalized = re.sub(r"[-_]", "", key).lower()
                if normalized in _CREDENTIAL_FIELDS or key.lower() in _RESERVED:
                    raise ValueError("extra_body 不能覆盖认证、模型、输入或流式请求字段")
                visit(child, depth + 1)
        elif isinstance(obj, list):
            for child in obj:
                visit(child, depth + 1)
    visit(value)
    return json.loads(serialized)


def _validate_profile(raw, existing=None):
    if not isinstance(raw, dict) or set(raw) - _FIELDS - _READ_ONLY:
        raise ValueError("模型配置字段无效")
    values = copy.deepcopy(existing) if existing else {}
    values.update({key: value for key, value in raw.items() if key in _FIELDS and key not in ("api_key", "clear_api_key")})
    profile_id = values.get("id") or uuid.uuid4().hex[:12]
    if not isinstance(profile_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", profile_id):
        raise ValueError("模型配置 ID 只能包含字母、数字、下划线和短横线")
    provider_type = values.get("type", "openai_compatible")
    if not isinstance(provider_type, str) or provider_type not in PROVIDER_TYPES:
        raise ValueError("不支持的模型接口类型")
    defaults = PROVIDER_TYPES[provider_type]
    name = values.get("name", defaults["name"])
    if not isinstance(name, str) or not name.strip() or len(name) > 80 or any(ord(c) < 32 for c in name):
        raise ValueError("模型名称长度须为 1 至 80 个字符")
    model = values.get("model", defaults["model"])
    if not isinstance(model, str) or len(model) > 200 or any(ord(c) < 32 or ord(c) == 127 for c in model):
        raise ValueError("模型 ID 无效")
    model = model.strip()
    enabled = values.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("enabled 必须是布尔值")
    clear = raw.get("clear_api_key", False)
    if not isinstance(clear, bool):
        raise ValueError("clear_api_key 必须是布尔值")
    key = raw.get("api_key", "")
    if not isinstance(key, str) or len(key) > 16384 or any(ord(c) < 32 or ord(c) == 127 for c in key):
        raise ValueError("API 密钥格式无效")
    key = "" if clear else key.strip() or (existing or {}).get("api_key", "")
    extra = _validate_extra(values.get("extra_body", {}))
    nested_field = "generationConfig" if provider_type == "gemini" else "options" if provider_type == "ollama" else None
    if nested_field and nested_field in extra and not isinstance(extra[nested_field], dict):
        raise ValueError(nested_field + " 必须为 JSON 对象")
    max_tokens = _number(values.get("max_tokens"), 1, 2_000_000, "max_tokens", integer=True)
    if max_tokens is not None and any(field in extra for field in ("max_completion_tokens", "max_output_tokens", "max_tokens")):
        raise ValueError("请只在一个位置设置输出 token 上限")
    return {
        "id": profile_id, "name": name.strip(), "type": provider_type,
        "base_url": _safe_url(values.get("base_url", defaults["base_url"])),
        "model": model, "enabled": enabled, "api_key": key,
        "temperature": _number(values.get("temperature"), 0, 1 if provider_type == "anthropic" else 2, "temperature"),
        "top_p": _number(values.get("top_p"), 0, 1, "top_p"),
        "max_tokens": max_tokens,
        "timeout": _number(values.get("timeout", 100), 5, 600, "timeout", nullable=False),
        "extra_body": extra,
    }


def _api_root(profile):
    """Preserve custom URL prefixes; accept both API roots and full endpoints."""
    url = profile["base_url"]
    kind = profile["type"]
    endings = {
        "chatgpt_web": ("/v1/chat/completions", "/v1"),
        "openai_compatible": ("/chat/completions", "/models"),
        "openai_responses": ("/responses", "/models"),
        "anthropic": ("/messages", "/models"),
        "gemini": ("/models",),
        "ollama": ("/chat", "/tags"),
    }[kind]
    for ending in endings:
        if url.endswith(ending):
            url = url[:-len(ending)]
            break
    path = urlsplit(url).path.rstrip("/")
    if not path:
        url += {"chatgpt_web": "", "gemini": "/v1beta", "ollama": "/api"}.get(kind, "/v1")
    return url


def _headers(profile):
    headers = {"Accept": "application/json"}
    key = profile["api_key"]
    if profile["type"] == "anthropic":
        headers["anthropic-version"] = "2023-06-01"
        if key:
            headers["x-api-key"] = key
    elif profile["type"] == "gemini":
        if key:
            headers["x-goog-api-key"] = key
    elif key:
        headers["Authorization"] = "Bearer " + key
    return headers


class ProviderManager:
    """Drop-in provider used by the kernel plus administrator profile operations."""

    def __init__(self, config, path):
        self.path = Path(path)
        self.lock = asyncio.Lock()
        self.session = None
        self.calls = 0
        self.last_latency_ms = 0
        self._health = {}
        self._profiles = {}
        self.default_id = None
        if self.path.exists():
            try:
                if self.path.is_symlink() or self.path.stat().st_size > 2 * 1024 * 1024:
                    raise ValueError
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict) or raw.get("version", 1) != 1 or not isinstance(raw.get("profiles"), list) or len(raw["profiles"]) > 100:
                    raise ValueError
                for item in raw["profiles"]:
                    profile = _validate_profile(item)
                    if profile["id"] in self._profiles:
                        raise ValueError
                    self._profiles[profile["id"]] = profile
                self.default_id = raw.get("default_id") or None
                if self.default_id is not None and (not isinstance(self.default_id, str) or self.default_id not in self._profiles):
                    raise ValueError
                self.path.chmod(0o600)
            except (OSError, ValueError, TypeError, KeyError, RecursionError):
                raise ValueError("私有模型配置无法读取；请修复该文件，原文件未被覆盖") from None
        else:
            getter = config.get if isinstance(config, dict) else lambda key, default: getattr(config, key, default)
            profile = _validate_profile({
                "id": "chatgpt-web", "name": "ChatGPT 网页", "type": "chatgpt_web",
                "base_url": getter("provider_url", "http://chatgpt-web:17656"),
                "api_key": getter("provider_token", ""), "model": "chatgpt-web",
            })
            self._profiles[profile["id"]] = profile
            self.default_id = profile["id"]
            self._save(self._profiles, self.default_id)

    def _save(self, profiles, default_id):
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            if self.path.is_symlink():
                raise OSError
            descriptor, temporary = tempfile.mkstemp(prefix="." + self.path.name + ".", suffix=".tmp", dir=self.path.parent)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                os.chmod(temporary, 0o600)
                json.dump({"version": 1, "default_id": default_id, "profiles": list(profiles.values())}, output,
                          ensure_ascii=False, indent=2, allow_nan=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            temporary = None
        except (OSError, ValueError, TypeError):
            raise ValueError("私有模型配置保存失败，当前配置未变更") from None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    async def start(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()

    async def close(self):
        async with self.lock:
            if self.session is not None:
                await self.session.close()

    def _select(self, profile_id=None, *, require_model=True):
        profile_id = self.default_id if profile_id is None else profile_id
        if not isinstance(profile_id, str) or profile_id not in self._profiles:
            raise ProviderError("尚未选择可用的默认模型，请在控制台配置模型")
        profile = copy.deepcopy(self._profiles[profile_id])
        if not profile["enabled"]:
            raise ProviderError("该模型配置已停用，请在控制台启用或切换模型")
        if require_model and not profile["model"]:
            raise ProviderError("请先填写模型 ID，或从服务商获取模型列表后选择")
        return profile

    def _public(self, profile):
        public = copy.deepcopy({key: value for key, value in profile.items() if key != "api_key"})
        public["has_api_key"] = bool(profile["api_key"])
        public["is_default"] = profile["id"] == self.default_id
        public["configured"] = bool(profile["base_url"] and profile["model"])
        public["health"] = self._status(profile["id"])
        return public

    def _status(self, profile_id):
        profile = self._profiles.get(profile_id)
        if profile is None:
            return {"healthy": False, "state": "not_configured"}
        if not profile["enabled"]:
            return {"healthy": False, "state": "disabled"}
        if not profile["model"]:
            return {"healthy": False, "state": "model_required"}
        return dict(self._health.get(profile_id, {"healthy": False, "state": "not_checked"}))

    def list_profiles(self):
        return {"profiles": [self._public(profile) for profile in self._profiles.values()],
                "default_id": self.default_id,
                "types": [{"id": key, **dict(value)} for key, value in PROVIDER_TYPES.items()]}

    def export_public(self):
        """Export portable settings without credentials or transient health."""
        return {"version": 1, "default_id": self.default_id,
                "profiles": [{key: copy.deepcopy(value) for key, value in profile.items() if key != "api_key"}
                             for profile in self._profiles.values()]}

    async def upsert(self, profile):
        if self.lock.locked():
            raise ValueError("模型正在回复，请稍后修改")
        async with self.lock:
            if not isinstance(profile, dict):
                raise ValueError("模型配置必须为对象")
            existing = self._profiles.get(profile.get("id")) if isinstance(profile.get("id"), str) else None
            validated = _validate_profile(profile, existing)
            if existing is None and len(self._profiles) >= 100:
                raise ValueError("模型配置数量已达到 100 个上限")
            profiles = copy.deepcopy(self._profiles)
            profiles[validated["id"]] = validated
            default_id = validated["id"] if not self._profiles else self.default_id
            self._save(profiles, default_id)
            self._profiles, self.default_id = profiles, default_id
            self._health.pop(validated["id"], None)
            return self._public(validated)

    async def remove(self, profile_id):
        if self.lock.locked():
            raise ValueError("模型正在回复，请稍后修改")
        async with self.lock:
            if not isinstance(profile_id, str) or profile_id not in self._profiles:
                raise ValueError("模型配置不存在")
            profiles = copy.deepcopy(self._profiles)
            del profiles[profile_id]
            default_id = None if profile_id == self.default_id else self.default_id
            self._save(profiles, default_id)
            self._profiles, self.default_id = profiles, default_id
            self._health.pop(profile_id, None)
            return self.list_profiles()

    async def set_default(self, profile_id):
        if self.lock.locked():
            raise ValueError("模型正在回复，请稍后修改")
        async with self.lock:
            if not isinstance(profile_id, str) or profile_id not in self._profiles:
                raise ValueError("模型配置不存在")
            profile = self._profiles[profile_id]
            if not profile["enabled"] or not profile["model"]:
                raise ValueError("默认模型须已启用并填写模型 ID")
            self._save(self._profiles, profile_id)
            self.default_id = profile_id
            return self.list_profiles()

    def snapshot(self):
        profile = self._profiles.get(self.default_id)
        health = self._status(self.default_id)
        return {**health, "calls": self.calls, "last_latency_ms": self.last_latency_ms,
                "busy": self.lock.locked(), "default_id": self.default_id,
                "id": profile["id"] if profile else None, "name": profile["name"] if profile else "未配置",
                "type": profile["type"] if profile else None, "model": profile["model"] if profile else None}

    async def _request(self, profile, method, url, *, payload=None, params=None, timeout=None):
        if self.session is None or self.session.closed:
            raise ProviderError("模型服务尚未启动")
        try:
            async with self.session.request(method, url, headers=_headers(profile), json=payload, params=params,
                                            timeout=aiohttp.ClientTimeout(total=timeout or profile["timeout"]),
                                            allow_redirects=False) as response:
                data = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    data.extend(chunk)
                    if len(data) > _MAX_RESPONSE_BYTES:
                        raise ProviderError("模型服务返回的数据过大")
                try:
                    raw = json.loads(data)
                except (ValueError, UnicodeError, RecursionError):
                    raw = None
                if not 200 <= response.status < 300:
                    if profile["type"] == "chatgpt_web" and isinstance(raw, dict) and isinstance(raw.get("error"), dict):
                        code = raw["error"].get("code")
                        if isinstance(code, str) and code in _WEB_ERRORS:
                            raise ProviderError(_WEB_ERRORS[code])
                    messages = {400: "模型参数被服务商拒绝，请检查模型 ID、采样参数和输出上限",
                                401: "模型认证失败，请检查该配置的 API 密钥", 402: "模型账户额度不足，请检查服务商账户",
                                403: "模型服务拒绝访问，请检查权限或服务商限制",
                                404: "模型接口或模型不存在，请检查服务地址和模型 ID", 429: "模型服务已限流或额度不足，请稍后再试"}
                    if 300 <= response.status < 400:
                        raise ProviderError("模型接口发生重定向，请填写最终 API 地址")
                    raise ProviderError(messages.get(response.status, "模型服务暂时不可用，请稍后重试"))
                if not isinstance(raw, dict):
                    raise ProviderError("模型服务没有返回有效的 JSON 对象")
                return raw
        except asyncio.TimeoutError:
            raise ProviderError("模型回复超时，本次没有自动重试") from None
        except (aiohttp.ClientError, OSError, ValueError):
            raise ProviderError("模型连接失败，请检查服务地址和网络") from None

    async def inspect(self):
        try:
            profile = self._select(require_model=False)
        except ProviderError:
            return self._status(self.default_id)
        if profile["type"] == "chatgpt_web":
            try:
                raw = await self._request(profile, "GET", _api_root(profile) + "/health", timeout=5)
                health = {key: raw[key] is True for key in ("browser_connected", "signed_in", "enabled") if key in raw}
                ready = raw.get("signed_in") is True and raw.get("enabled") is True and raw.get("browser_connected", True) is True
                health.update(healthy=ready, state="ready" if ready else "login_required")
                count = raw.get("completed_requests")
                if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                    health["completed_requests"] = count
            except ProviderError as error:
                health = {"healthy": False, "state": "unreachable", "last_error": str(error)}
        else:
            previous = dict(self._health.get(profile['id'], {}))
            try:
                models = await self.list_models(profile["id"])
                health = ({**previous} if previous.get('generation_verified') or previous.get('state') == 'error'
                          else {'healthy': False, 'state': 'reachable' if profile['model'] else 'model_required', 'generation_verified': False})
                health.update(models_available=True, available_models=len(models['models']))
                health.pop('models_error', None)
            except ProviderError as error:
                # /models is optional on compatible gateways. A listing failure
                # cannot invalidate a successful generation, or prove that a
                # never-tested generation endpoint is broken.
                health = ({**previous} if previous.get('generation_verified') or previous.get('state') == 'error'
                          else {'healthy': False, 'state': 'models_unavailable', 'generation_verified': False})
                health.update(models_available=False, models_error=str(error))
        self._health[profile["id"]] = health
        return dict(health)

    async def list_models(self, profile_id):
        profile = self._select(profile_id, require_model=False)
        if profile["type"] == "chatgpt_web":
            return {"provider_id": profile["id"], "models": [{"id": profile["model"], "name": profile["model"]}], "source": "configured", "truncated": False}
        root, kind = _api_root(profile), profile["type"]
        endpoint = "/tags" if kind == "ollama" else "/models"
        models, params, seen_pages = {}, {}, set()
        truncated = False
        for page in range(20):
            raw = await self._request(profile, "GET", root + endpoint, params=params, timeout=min(profile["timeout"], 20))
            rows = raw.get("models") if kind in ("ollama", "gemini") else raw.get("data")
            if not isinstance(rows, list):
                raise ProviderError("模型列表格式不符合所选接口类型")
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if kind == "gemini" and "supportedGenerationMethods" in row:
                    methods = row.get("supportedGenerationMethods")
                    if not isinstance(methods, list) or "generateContent" not in methods:
                        continue
                model_id = row.get("name") if kind in ("ollama", "gemini") else row.get("id")
                if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 200 or (profile["api_key"] and profile["api_key"] in model_id):
                    continue
                if kind == "gemini":
                    model_id = model_id.removeprefix("models/")
                name = row.get("displayName") if kind == "gemini" else row.get("display_name", model_id)
                if not isinstance(name, str) or (profile["api_key"] and profile["api_key"] in name):
                    name = model_id
                models[model_id] = {"id": model_id, "name": name[:200]}
                if len(models) >= 2000:
                    truncated = True
                    break
            cursor = raw.get("nextPageToken") if kind == "gemini" else raw.get("last_id") if kind == "anthropic" and raw.get("has_more") else None
            if truncated or not cursor:
                break
            if not isinstance(cursor, str) or len(cursor) > 2048 or cursor in seen_pages:
                raise ProviderError("模型列表分页返回无效")
            seen_pages.add(cursor)
            params = {"pageToken": cursor} if kind == "gemini" else {"after_id": cursor}
            if page == 19:
                truncated = True
        return {"provider_id": profile["id"], "models": list(models.values()), "source": "remote", "truncated": truncated}

    @staticmethod
    def _generation_request(profile, prompt):
        kind = profile["type"]
        body = copy.deepcopy(profile["extra_body"])
        model, root = profile["model"], _api_root(profile)
        if kind in ("chatgpt_web", "openai_compatible"):
            endpoint = "/v1/chat/completions" if kind == "chatgpt_web" else "/chat/completions"
            body.update(model=model, stream=False, messages=[{"role": "user", "content": prompt}])
            if kind == "chatgpt_web":
                body["yukino_raw_prompt"] = True
            for field in ("temperature", "top_p", "max_tokens"):
                if profile[field] is not None:
                    body[field] = profile[field]
        elif kind == "openai_responses":
            endpoint = "/responses"
            body.update(model=model, stream=False, input=[{"role": "user", "content": prompt}])
            for field in ("temperature", "top_p"):
                if profile[field] is not None:
                    body[field] = profile[field]
            if profile["max_tokens"] is not None:
                body["max_output_tokens"] = profile["max_tokens"]
        elif kind == "anthropic":
            endpoint = "/messages"
            body.update(model=model, stream=False, messages=[{"role": "user", "content": prompt}])
            body["max_tokens"] = profile["max_tokens"] or body.get("max_tokens", 1024)
            for field in ("temperature", "top_p"):
                if profile[field] is not None:
                    body[field] = profile[field]
        elif kind == "gemini":
            endpoint = "/models/" + quote(model.removeprefix("models/"), safe="") + ":generateContent"
            body["contents"] = [{"role": "user", "parts": [{"text": prompt}]}]
            generation = body.setdefault("generationConfig", {})
            if not isinstance(generation, dict):
                raise ProviderError("Gemini generationConfig 必须是 JSON 对象")
            for field, wire in (("temperature", "temperature"), ("top_p", "topP"), ("max_tokens", "maxOutputTokens")):
                if profile[field] is not None:
                    generation[wire] = profile[field]
        else:
            endpoint = "/chat"
            body.update(model=model, stream=False, messages=[{"role": "user", "content": prompt}])
            options = body.setdefault("options", {})
            if not isinstance(options, dict):
                raise ProviderError("Ollama options 必须是 JSON 对象")
            for field, wire in (("temperature", "temperature"), ("top_p", "top_p"), ("max_tokens", "num_predict")):
                if profile[field] is not None:
                    options[wire] = profile[field]
        return root + endpoint, body

    @staticmethod
    def _text(profile, raw):
        try:
            kind = profile["type"]
            if kind in ("chatgpt_web", "openai_compatible"):
                content = raw["choices"][0]["message"]["content"]
                if isinstance(content, list):
                    content = "".join(item["text"] for item in content if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str))
            elif kind == "openai_responses":
                if raw.get("status") in ("failed", "cancelled", "queued", "in_progress", "incomplete") or raw.get("error"):
                    raise ProviderError("模型响应未完成，请检查输出上限或服务状态")
                content = "".join(part["text"] for item in raw["output"] if isinstance(item, dict) and item.get("type") == "message"
                                  for part in item.get("content", []) if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str))
            elif kind == "anthropic":
                content = "".join(part["text"] for part in raw["content"] if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str))
            elif kind == "gemini":
                content = "".join(part["text"] for part in raw["candidates"][0]["content"]["parts"]
                                  if isinstance(part, dict) and isinstance(part.get("text"), str) and not part.get("thought"))
            else:
                if raw.get("error"):
                    raise ProviderError("Ollama 返回模型错误，请检查服务和模型 ID")
                content = raw["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ProviderError("模型没有返回可发送的文字")
            # Malformed gateways must not echo a configured key into QQ or the admin test result.
            if profile["api_key"]:
                content = content.replace(profile["api_key"], "[已隐藏密钥]")
            return content.strip()
        except (KeyError, IndexError, TypeError, ValueError):
            raise ProviderError("模型返回格式不符合所选接口类型") from None

    async def complete(self, prompt, timeout=100, provider_id=None):
        if not isinstance(prompt, str) or not prompt.strip():
            raise ProviderError("模型输入不能为空")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ProviderError("模型超时设置无效")
        async with self.lock:
            profile = self._select(provider_id)
            self.calls += 1
            started = time.monotonic()
            try:
                url, body = self._generation_request(profile, prompt)
                raw = await self._request(profile, "POST", url, payload=body, timeout=min(timeout, profile["timeout"]))
                text = self._text(profile, raw)
                self._health[profile["id"]] = {"healthy": True, "state": "ready", 'generation_verified': True}
                return text
            except ProviderError as error:
                self._health[profile["id"]] = {"healthy": False, "state": "error", "last_error": str(error)}
                raise
            finally:
                self.last_latency_ms = round((time.monotonic() - started) * 1000)

    async def test(self, profile_id, text="只回复OK"):
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError("测试文本须为 1 至 2000 个字符")
        profile = self._select(profile_id)
        started = time.monotonic()
        answer = await self.complete(text, timeout=profile["timeout"], provider_id=profile["id"])
        return {"ok": True, "provider_id": profile["id"], "model": profile["model"], "text": answer,
                "latency_ms": round((time.monotonic() - started) * 1000)}
