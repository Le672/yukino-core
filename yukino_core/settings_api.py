"""Authenticated settings routes. Credentials are write-only and never exported."""
from __future__ import annotations

import asyncio
import copy
import json
import platform
import time
from pathlib import Path

from aiohttp import web

from . import __version__
from .config import Config, settings_schema
from .provider import ProviderError
from .transport import OneBotClient
from .plugin_packages import install_package, reload_plugins, remove_package


def install_settings_routes(app, kernel, ok, failure, body, re_scope):
    change_lock = asyncio.Lock()

    def idle():
        return not kernel.active and not kernel.pending_count and not kernel.provider.lock.locked() and not kernel.rich.busy

    async def reconnect():
        old = kernel.transport
        await old.stop()
        c = kernel.config
        details = c.connection_data
        kernel.transport = OneBotClient(c.onebot_url, c.onebot_token, kernel.on_event,
            self_id=c.self_id, action_timeout=details['action_timeout'],
            reconnect_min=details['reconnect_min'], reconnect_max=details['reconnect_max'])
        kernel.qq_status = {'online': None, 'user_id': c.self_id}
        if c.receive_enabled: await kernel.transport.start()

    async def schema(request): return ok(settings_schema())

    async def connections(request):
        if request.method == 'GET': return ok(kernel.config.connections())
        async with change_lock:
            if not idle(): return failure('busy', '还有消息正在处理，请等待队列清空后修改平台连接', 409)
            data = await body(request)
            try: result = kernel.config.update_connections(data)
            except ValueError as exc: return failure('invalid_connection', str(exc))
            await reconnect()
            return ok({**result, 'applied': True})

    async def connection_reconnect(request):
        async with change_lock:
            if not idle(): return failure('busy', '还有消息正在处理，请稍后重连', 409)
            await reconnect()
            return ok({'reconnecting': kernel.config.receive_enabled})

    async def providers(request):
        if request.method == 'GET': return ok(kernel.provider.list_profiles())
        try:
            result = await kernel.provider.upsert(await body(request))
            return ok(result if result is not None else kernel.provider.list_profiles())
        except ValueError as exc: return failure('invalid_provider', str(exc))

    async def provider_action(request):
        pid = request.match_info['id']
        try:
            if request.method == 'DELETE':
                if pid in getattr(kernel.config, 'session_providers', {}).values():
                    return failure('provider_in_use', '该模型仍被会话单独指定，请先把这些会话改为跟随默认模型')
                await kernel.provider.remove(pid)
            elif request.match_info.get('action') == 'default': await kernel.provider.set_default(pid)
            elif request.match_info.get('action') == 'models': return ok(await kernel.provider.list_models(pid))
            else:
                data = await body(request)
                text = data.get('text', '只回复OK')
                if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000: raise ValueError('测试内容长度为1—2000字符')
                if not idle(): return failure('busy', '模型正在回复，请稍后测试', 409)
                return ok(await kernel.provider.test(pid, text.strip()))
            return ok(kernel.provider.list_profiles())
        except ValueError as exc: return failure('invalid_provider', str(exc))

    async def session_provider(request):
        sid = request.match_info['id']
        if not re_scope(sid): raise ValueError()
        data = await body(request)
        pid = data.get('provider_id')
        if not isinstance(pid, str): raise ValueError()
        available = kernel.provider.list_profiles()['profiles']
        if pid and not any(p['id'] == pid and p['enabled'] for p in available):
            return failure('invalid_provider', '请选择已启用的模型实例')
        previous = dict(kernel.config.session_providers)
        if pid: kernel.config.session_providers[sid] = pid
        else: kernel.config.session_providers.pop(sid, None)
        try: kernel.config.save()
        except Exception:
            kernel.config.session_providers = previous
            raise
        return ok({'session_id': sid, 'provider_id': pid, 'applies_to': 'next_request'})

    async def validate(request):
        data = await body(request)
        try: result = Config.validate(data.get('config', data))
        except ValueError as exc: return failure('invalid_config', str(exc))
        return ok({'valid': True, 'config': result})

    async def export(request):
        return ok({'format': 'yukino-core-settings', 'schema_version': 1, 'version': __version__,
            'config': copy.deepcopy(kernel.config.data), 'connections': kernel.config.connections(),
            'providers': kernel.provider.export_public(),
            'plugin_flags': dict(kernel.config.plugin_flags),
            'plugin_settings': copy.deepcopy(kernel.config.plugin_settings),
            'session_providers': dict(kernel.config.session_providers),
            'credentials_included': False, 'history_included': False})

    async def import_settings(request):
        # Import is intentionally limited to general settings. Model credentials and
        # network changes are applied through their dedicated forms, one operation at a time.
        data = await body(request)
        if data.get('format') not in (None, 'yukino-core-settings'): raise ValueError()
        try:
            patch = Config.validate(data.get('config', {}))
            flags = data.get('plugin_flags', {})
            settings = data.get('plugin_settings', {})
            if not isinstance(flags, dict) or any(not isinstance(v, bool) or not any(i['id']==k for i in kernel.plugins.items) for k,v in flags.items()):
                raise ValueError('插件开关无效，目标实例需要先安装对应插件')
            if not isinstance(settings, dict): raise ValueError('插件设置无效')
            validated = {k: kernel.plugins.validate_settings(k, v) for k,v in settings.items()}
        except ValueError as exc: return failure('invalid_import', str(exc))
        old = (copy.deepcopy(kernel.config.data), dict(kernel.config.plugin_flags), copy.deepcopy(kernel.config.plugin_settings))
        try:
            kernel.config.data.update(patch)
            kernel.config.plugin_flags.update(flags)
            kernel.config.plugin_settings.update(validated)
            kernel.config.save()
        except Exception:
            kernel.config.data, kernel.config.plugin_flags, kernel.config.plugin_settings = old
            raise
        for pid, enabled in flags.items():
            if not enabled: await kernel.plugins._kill(pid)
        return ok({'imported': True, 'fields': list(patch),
            'connections_imported': False, 'providers_imported': False,
            'message': '已导入对话设置和已安装插件参数。模型、凭据、平台连接及会话路由请在对应页面设置，避免切断当前连接。'})

    async def plugin_config(request):
        pid = request.match_info['id']
        try:
            if request.method == 'PUT':
                data = await body(request)
                kernel.plugins.update_settings(pid, data.get('values'))
            return ok(kernel.plugins.get_settings(pid))
        except ValueError as exc: return failure('invalid_plugin_config', str(exc))

    async def plugin_package(request):
        try:
            if request.method == 'DELETE': return ok(await remove_package(kernel.plugins,request.match_info['id']))
            if request.path.endswith('/reload'): return ok(await reload_plugins(kernel.plugins))
            if request.content_type!='multipart/form-data': raise ValueError('请通过文件选择器上传ZIP')
            reader=await request.multipart()
            part=await reader.next()
            if part is None or part.name!='file': raise ValueError('请选择插件ZIP文件')
            payload=bytearray()
            while chunk:=await part.read_chunk(16384):
                payload.extend(chunk)
                if len(payload)>2_000_000: return failure('file_too_large','插件ZIP不能超过2MB',413)
            if await reader.next() is not None: raise ValueError('每次只能安装一个插件包')
            return ok(await install_package(kernel.plugins,bytes(payload)))
        except ValueError as exc: return failure('invalid_plugin_package',str(exc))

    async def system_info(request):
        return ok({'version': __version__, 'python': platform.python_version(), 'platform': platform.system(),
            'config_schema_version': 1, 'uptime': int(time.monotonic()-kernel.started),
            'features': ['multi_provider','per_session_provider','onebot_v11','live_settings','resource_editor','plugin_settings','redacted_export'],
            'paths': {'data': '实例数据目录', 'config': 'config.json', 'providers': 'providers.json（含私密密钥）', 'connections': 'connections.json（含私密令牌）'},
            'release': {'channel':'release-candidate','core_license':'MIT','third_party_notice':'THIRD_PARTY_NOTICES.md',
                        'platforms':['OneBot v11 正向 WebSocket'], 'plugin_api':'handle(text, context)',
                        'maibot_plugin_compatible': False, 'astrbot_plugin_compatible': False}})

    async def diagnostics(request):
        snapshot = await kernel.status()
        return ok({'version': __version__, 'status': snapshot, 'config_schema':1,
                   'providers': kernel.provider.list_profiles(), 'connections': kernel.config.connections(),
                   'credentials_included':False, 'prompts_included':False})

    app.router.add_get('/api/settings/schema', schema)
    app.router.add_get('/api/connections', connections)
    app.router.add_put('/api/connections', connections)
    app.router.add_post('/api/connections/reconnect', connection_reconnect)
    app.router.add_get('/api/providers', providers)
    app.router.add_post('/api/providers', providers)
    app.router.add_delete('/api/providers/{id}', provider_action)
    app.router.add_post('/api/providers/{id}/{action:default|test}', provider_action)
    app.router.add_get('/api/providers/{id}/{action:models}', provider_action)
    app.router.add_put('/api/sessions/{id}/provider', session_provider)
    app.router.add_post('/api/config/validate', validate)
    app.router.add_get('/api/config/export', export)
    app.router.add_post('/api/config/import', import_settings)
    app.router.add_get('/api/plugins/{id}/config', plugin_config)
    app.router.add_put('/api/plugins/{id}/config', plugin_config)
    app.router.add_post('/api/plugins/install',plugin_package)
    app.router.add_post('/api/plugins/reload',plugin_package)
    app.router.add_delete('/api/plugins/{id}',plugin_package)
    app.router.add_get('/api/system/info', system_info)
    app.router.add_get('/api/system/diagnostics', diagnostics)
