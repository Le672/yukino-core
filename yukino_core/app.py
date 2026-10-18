from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import secrets
import shutil
import time
from pathlib import Path
from urllib.parse import quote

from aiohttp import web

from .config import Config
from .core import Kernel
from .plugins import PluginManager
from .provider import ProviderError
from .models import ProviderManager
from .settings_api import install_settings_routes
from .resources import ResourceLibrary
from .store import Store
from .transport import OneBotClient
from . import __version__
from . import video_session


def ok(data=None): return web.json_response({"ok": True, "data": data}, dumps=lambda obj: json.dumps(obj, ensure_ascii=False))


def failure(code, message, status=400):
    return web.json_response({"ok": False, "error": {"code": code, "message": message}}, status=status)


async def body(request):
    raw = bytearray()
    large_settings = request.path in ('/api/config','/api/config/validate','/api/config/import','/api/providers')
    large_settings = large_settings or (request.path.startswith('/api/plugins/') and request.path.endswith('/config'))
    limit = 256000 if large_settings else 65536
    async for chunk in request.content.iter_chunked(16384):
        raw.extend(chunk)
        if len(raw) > limit: raise web.HTTPRequestEntityTooLarge(max_size=limit, actual_size=len(raw))
    data = json.loads(raw)
    if not isinstance(data, dict): raise ValueError()
    return data


def protected(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return response


def create_app(kernel):
    sessions, attempts = {}, {}

    @web.middleware
    async def security(request, handler):
        try:
            if request.path.startswith("/api/"):
                if request.method not in ("GET", "HEAD"):
                    origin = request.headers.get("Origin")
                    if origin and origin.rstrip("/") != f"{request.scheme}://{request.host}":
                        return protected(failure("origin", "请求来源不匹配", 403))
                if request.path != "/api/login":
                    session = sessions.get(request.cookies.get("yukino_session", ""))
                    if not session or session["expires"] < time.monotonic(): return protected(failure("unauthorized", "请先登录", 401))
                    request["auth"] = session
                    if request.method not in ("GET", "HEAD") and not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), session["csrf_token"]):
                        return protected(failure("csrf", "安全校验失败，请刷新页面后重试", 403))
            response = await handler(request)
        except web.HTTPException as exc:
            if request.path.startswith("/api/"): response = failure("http_error", "请求无效", exc.status)
            else: raise
        except (ValueError, TypeError, KeyError): response = failure("invalid_request", "请求字段或参数无效")
        except ProviderError as exc: response = failure("provider_unavailable", str(exc), 503)
        except Exception:
            # Never include exception repr or tokens in a public response.
            response = failure("internal_error", "服务暂时无法完成请求", 500)
        return protected(response)

    app = web.Application(middlewares=[security], client_max_size=6_000_000)
    app["kernel"] = kernel

    async def login(request):
        data = await body(request)
        key = request.remote or "local"
        now = time.monotonic()
        record = attempts.get(key, (now, 0))
        if now - record[0] > 60: record = (now, 0)
        if record[1] >= 12: return failure("rate_limited", "尝试次数过多，稍后再试", 429)
        token = data.get("token")
        if not isinstance(token, str) or not hmac.compare_digest(token, kernel.config.admin_token):
            if len(attempts) > 256: attempts.clear()
            attempts[key] = (record[0], record[1] + 1)
            return failure("unauthorized", "管理令牌不正确", 401)
        attempts.pop(key, None)
        for sid in list(sessions):
            if sessions[sid]["expires"] < now: sessions.pop(sid, None)
        if len(sessions) >= 32: sessions.pop(next(iter(sessions)))
        sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        sessions[sid] = {"csrf_token": csrf, "expires": now + 43200}
        response = web.json_response({"ok": True, "csrf_token": csrf})
        response.set_cookie("yukino_session", sid, httponly=True, samesite="Strict", secure=request.secure, max_age=43200, path="/")
        return response

    async def me(request): return ok({"csrf_token": request["auth"]["csrf_token"], "name": kernel.config.data["name"]})
    async def logout(request):
        sessions.pop(request.cookies.get("yukino_session", ""), None)
        response = ok(); response.del_cookie("yukino_session", path="/"); return response
    async def status(request): return ok(await kernel.status())
    async def config(request):
        if request.method == "PUT":
            try: return ok(kernel.config.update(await body(request)))
            except ValueError as exc: return failure("invalid_config", str(exc))
        return ok(dict(kernel.config.data))
    async def session_list(request):
        rows = await kernel.store.list_sessions()
        routes = getattr(kernel.config, 'session_providers', {})
        return ok([{**row, 'provider_id': routes.get(row['session_id'], '')} for row in rows])
    async def messages(request):
        rows = await kernel.store.history(request.match_info["id"], limit=100, include_archived=True)
        for row in rows:
            for attachment in row.get("attachments", []):
                attachment["preview_url"] = "/api/resources/images/" + quote(attachment["resource_id"], safe="") + "/file"
        return ok(rows)
    async def reset(request): await kernel.reset(request.match_info["id"]); return ok()
    async def memories(request):
        scope = request.query.get("scope", "")
        if request.method == "POST":
            data = await body(request); scope, text = data.get("scope"), data.get("text")
            if not isinstance(scope, str) or not re_scope(scope) or not isinstance(text, str) or not 1 <= len(text.strip()) <= 1000: raise ValueError()
            return ok({"id": await kernel.store.remember(scope, text.strip())})
        if not re_scope(scope): raise ValueError()
        if request.method == "DELETE": return ok({"deleted": await kernel.store.forget(scope, int(request.match_info["id"]))})
        if request.method == "PATCH":
            data = await body(request)
            text = data.get("text")
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= 1000: raise ValueError()
            return ok({"updated": await kernel.store.update_memory(scope, int(request.match_info["id"]), text.strip())})
        return ok(await kernel.store.recall(scope, limit=100))
    async def plugins(request): return ok(kernel.plugins.list())
    async def toggle(request):
        data = await body(request); await kernel.plugins.toggle(data["id"], data["enabled"]); return ok(kernel.plugins.list())
    async def batch(request):
        data = await body(request)
        if not isinstance(data.get("enabled"), bool): raise ValueError()
        for plugin in kernel.plugins.items: await kernel.plugins.toggle(plugin["id"], data["enabled"])
        return ok(kernel.plugins.list())
    async def resources(request): return ok(await kernel.resources.status())

    async def site_session(request):
        if request.method == "POST":
            data = await body(request)
            return ok(video_session.save(kernel.plugins.root, data.get("cookie")))
        return ok(video_session.status(kernel.plugins.root))

    def public_item(item):
        allowed = ("id", "kind", "enabled", "description", "tags", "text", "meaning", "situation", "style", "examples", "title")
        result = {key: item[key] for key in allowed if key in item}
        if result.get("kind") == "images": result["preview_url"] = "/api/resources/images/" + quote(result["id"], safe="") + "/file"
        return result

    def resource_kind(request):
        kind = request.match_info["kind"]
        if kind not in ("images", "jargon", "expressions", "knowledge"): raise web.HTTPNotFound()
        return kind

    async def resource_list(request):
        kind = resource_kind(request)
        if request.method == "POST":
            data = await body(request)
            try: return ok(public_item(await kernel.resources.create_item(kind, data)))
            except ValueError as exc: return failure("invalid_resource", str(exc)[:200])
        offset, limit = int(request.query.get("offset", 0)), int(request.query.get("limit", 24))
        if offset < 0 or not 1 <= limit <= 100: raise ValueError()
        result = await kernel.resources.list_items(kind, q=request.query.get("q", "")[:500], offset=offset, limit=limit,
                                                   include_disabled=request.query.get("include_disabled", "false") == "true")
        result["items"] = [public_item(item) for item in result["items"]]
        return ok(result)

    async def resource_item(request):
        kind, rid = resource_kind(request), request.match_info["id"]
        try:
            if request.method == "PATCH": return ok(public_item(await kernel.resources.update_item(kind, rid, await body(request))))
            if request.method == "DELETE": return ok({"deleted": await kernel.resources.delete_item(kind, rid)})
            return ok(public_item(await kernel.resources.get_item(kind, rid)))
        except KeyError: raise web.HTTPNotFound()
        except ValueError as exc: return failure("invalid_resource", str(exc)[:200])

    async def resource_file(request):
        path = await kernel.resources.image_file(request.match_info["id"])
        if path is None: raise web.HTTPNotFound()
        response = web.FileResponse(path)
        response.headers["Content-Disposition"] = "inline"
        return response

    async def resource_upload(request):
        if request.content_length and request.content_length > 5_100_000:
            return failure("file_too_large", "图片不能超过5MB", 413)
        if request.content_type != "multipart/form-data": return failure("invalid_upload", "请通过图片上传表单提交")
        reader = await request.multipart()
        fields, payload, filename, count = {}, None, "", 0
        while (part := await reader.next()) is not None:
            count += 1
            if count > 3 or part.name not in ("file", "description", "tags") or part.name in fields:
                return failure("invalid_upload", "上传字段无效或重复")
            limit = 5_000_000 if part.name == "file" else 16000
            raw = bytearray()
            while chunk := await part.read_chunk(16384):
                raw.extend(chunk)
                if len(raw) > limit: return failure("file_too_large", "图片或文字字段超过限制", 413)
            fields[part.name] = True
            if part.name == "file": payload, filename = bytes(raw), part.filename or "image"
            else: fields[part.name] = raw.decode("utf-8")
        if payload is None: return failure("invalid_upload", "请选择图片")
        tags = json.loads(fields.get("tags", "[]"))
        try:
            item = await kernel.resources.upload_image(filename, payload, fields.get("description", ""), tags)
            return ok(public_item(item))
        except ValueError as exc: return failure("invalid_image", str(exc)[:200])
    async def events(request): return ok(await kernel.store.recent_events())
    async def test(request):
        data = await body(request); text = data.get("text")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 4000: raise ValueError()
        # Tests cannot starve the user queue or overlap an already running webpage job.
        if kernel.active or kernel.provider.lock.locked(): return failure("busy", "模型正在回复，请稍后测试", 409)
        return ok(await kernel.isolated_test(text.strip()))
    async def index(request): return web.FileResponse(Path(__file__).with_name("static") / "index.html")
    async def health(request): return ok({"kernel": "Yukino Core", "version": __version__, "ready": True})

    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_static("/static", Path(__file__).with_name("static"), show_index=False)
    app.router.add_post("/api/login", login)
    app.router.add_get("/api/me", me)
    app.router.add_post("/api/logout", logout)
    app.router.add_get("/api/status", status)
    app.router.add_get("/api/config", config)
    app.router.add_put("/api/config", config)
    app.router.add_get("/api/sessions", session_list)
    app.router.add_get("/api/sessions/{id}/messages", messages)
    app.router.add_post("/api/sessions/{id}/reset", reset)
    app.router.add_get("/api/memories", memories)
    app.router.add_post("/api/memories", memories)
    app.router.add_delete("/api/memories/{id}", memories)
    app.router.add_patch("/api/memories/{id}", memories)
    app.router.add_get("/api/plugins", plugins)
    app.router.add_post("/api/plugins/toggle", toggle)
    app.router.add_post("/api/plugins/batch", batch)
    app.router.add_get("/api/plugins/video/session", site_session)
    app.router.add_post("/api/plugins/video/session", site_session)
    app.router.add_get("/api/resources", resources)
    app.router.add_post("/api/resources/images/upload", resource_upload)
    app.router.add_get("/api/resources/images/{id}/file", resource_file)
    app.router.add_get("/api/resources/{kind}", resource_list)
    app.router.add_post("/api/resources/{kind}", resource_list)
    app.router.add_get("/api/resources/{kind}/{id}", resource_item)
    app.router.add_patch("/api/resources/{kind}/{id}", resource_item)
    app.router.add_delete("/api/resources/{kind}/{id}", resource_item)
    app.router.add_get("/api/events", events)
    app.router.add_post("/api/test", test)
    install_settings_routes(app, kernel, ok, failure, body, re_scope)
    return app


def re_scope(value):
    return isinstance(value, str) and len(value) <= 80 and value[:2] in ("p:", "g:") and value[2:].isdigit()


async def make_runtime(config, data_root, resource_root, legacy_root=None):
    store = Store(Path(data_root) / "core.db")
    await store.open()
    resources = ResourceLibrary(resource_root, legacy_root)
    await resources.open()
    provider = ProviderManager(config, Path(data_root) / 'providers.json')
    await provider.start()
    plugin_root = Path(data_root) / "plugins"
    plugin_root.mkdir(parents=True, exist_ok=True)
    calculator = plugin_root / "calculator.py"
    if not calculator.exists():
        shutil.copy2(Path(__file__).with_name("plugins") / "calculator.py", calculator)
    plugins = PluginManager(config, plugin_root)
    kernel = Kernel(config, store, resources, provider, plugins)
    connection = config.connection_data
    kernel.transport = OneBotClient(config.onebot_url, config.onebot_token, kernel.on_event, self_id=config.self_id,
        action_timeout=connection['action_timeout'], reconnect_min=connection['reconnect_min'], reconnect_max=connection['reconnect_max'])
    app = create_app(kernel)

    async def watch_health():
        while True:
            await provider.inspect()
            if config.receive_enabled and kernel.transport.snapshot()["connected"]:
                try:
                    login = await kernel.transport.call("get_login_info", timeout=4)
                    status = await kernel.transport.call("get_status", timeout=4)
                    kernel.qq_status = {"online": bool(status.get("online")), "user_id": str(login.get("user_id", ""))}
                except Exception:
                    kernel.qq_status = {"online": None, "user_id": config.self_id}
            await asyncio.sleep(config.data['health_interval'])

    async def startup(app):
        app["health_task"] = asyncio.create_task(watch_health())
        if config.receive_enabled: await kernel.transport.start()
        await kernel.native.start()
    async def cleanup(app):
        app["health_task"].cancel()
        with contextlib.suppress(asyncio.CancelledError): await app["health_task"]
        await kernel.transport.stop()
        await kernel.close()
        await provider.close()
        await store.close()
    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    return app
