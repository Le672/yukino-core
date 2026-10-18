"""Portable-launcher state and Windows lifecycle tests on isolated fixture data.

No VPS, real QQ account, external model, browser, or user installation is touched.
Installer tests use the real Config/PluginManager and tiny original source ZIPs.
"""
from __future__ import annotations

import asyncio
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from yukino_core.config import Config
from yukino_core.plugin_packages import install_package, remove_package
from yukino_core.plugins import PluginManager


HERE = Path(__file__).resolve().parent
SOURCE = HERE.parents[1]
CORE_SOURCE = SOURCE if (SOURCE/'scripts/init_instance.py').is_file() else SOURCE/'app'
SPEC = importlib.util.spec_from_file_location('portable_launcher_test_subject', SOURCE/'windows/launcher.py')
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)
PID = 'portable_probe'
FIXTURE_TOKEN = 'fixture-only-local-token-not-a-real-account'


def tiny_package():
    manifest = {
        'id': PID, 'name': 'Portable fixture', 'version': '1.0.0',
        'description': 'Original isolated test fixture', 'worker': 'worker.py',
        'commands': ['/portable-probe'],
        'config_schema': [{'key': 'probe_value', 'type': 'integer', 'label': 'Fixture parameter', 'default': 1, 'min': 1, 'max': 10}],
    }
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w', zipfile.ZIP_DEFLATED) as package:
        package.writestr('plugin.json', json.dumps(manifest))
        package.writestr('worker.py', "def handle(text, context):\n    return str(context.get('settings', {}).get('probe_value', 1))\n")
    return result.getvalue()


@unittest.skipUnless(os.name == 'nt', 'Windows launcher and msvcrt tests')
class LauncherTests(unittest.TestCase):
    def setUp(self):
        # Explicit fixture root stays inside this test's writable source directory.
        self.temporary = tempfile.TemporaryDirectory(prefix='.launcher-test-', dir=HERE)
        self.root = Path(self.temporary.name)
        self.data = self.root/'data'
        scripts = self.root/'app/scripts'; scripts.mkdir(parents=True)
        shutil.copy2(CORE_SOURCE/'scripts/init_instance.py', scripts/'init_instance.py')
        shutil.copy2(CORE_SOURCE/'config.example.json', self.root/'app/config.example.json')
        packages = self.root/'plugin-packages'; packages.mkdir()
        self.payload = tiny_package()
        self.entry = {'id': PID, 'archive': PID+'-1.0.0.zip',
                      'sha256': hashlib.sha256(self.payload).hexdigest(),
                      'settings': {'probe_value': 7, 'timeout': 3}}
        (packages/self.entry['archive']).write_bytes(self.payload)
        self.write_pack([self.entry])
        self.root_patch = patch.object(launcher, 'ROOT', self.root)
        self.root_patch.start()
        # initialize deliberately removes inherited YUKINO variables; restore all
        # process environment entries after each test without displaying any.
        self.env_patch = patch.dict(os.environ, {'YUKINO_ADMIN_TOKEN': FIXTURE_TOKEN})
        self.env_patch.start()
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(self.env_patch.stop)
        self.addCleanup(self.root_patch.stop)

    def write_pack(self, entries):
        (self.root/'plugin-packages/manifest.json').write_text(json.dumps({'plugins': entries}), encoding='utf-8')

    def config(self):
        return Config(self.data/'config.json')

    def marker(self):
        return launcher.read_json(self.data/'bundled-plugins.json')

    def package_directories(self):
        return sorted(p.name for p in (self.data/'plugins/packages').iterdir() if p.is_dir())

    def fixture_runtime(self):
        self.data.mkdir(exist_ok=True)
        state = {'port': 18801, 'instance': 'isolated-fixture-instance', 'pid': 123456}
        (self.data/'runtime.json').write_text(json.dumps(state), encoding='utf-8')
        (self.data/'secrets.json').write_text(json.dumps({'admin_token': FIXTURE_TOKEN}), encoding='utf-8')
        return state

    def test_fresh_instance_uses_real_initializer_installer_and_safe_defaults(self):
        launcher.initialize(self.data)
        cfg = self.config(); manager = PluginManager(cfg, self.data/'plugins')
        self.assertFalse(cfg.receive_enabled)
        self.assertEqual(cfg.owner_ids, [])
        self.assertEqual(launcher.read_json(self.data/'providers.json')['profiles'], [])
        self.assertNotEqual(launcher.read_json(self.data/'secrets.json')['admin_token'], FIXTURE_TOKEN)
        self.assertNotIn('YUKINO_ADMIN_TOKEN', os.environ)
        self.assertEqual(os.environ['YUKINO_SECRETS_FILE'], str(self.data/'secrets.json'))
        self.assertTrue(any(i['id'] == PID for i in manager.items))
        self.assertTrue(cfg.plugin_flags[PID])
        self.assertEqual(manager.settings(PID)['probe_value'], 7)
        self.assertEqual(self.marker()['ids'], [PID])
        self.assertFalse((self.data/'bundled-install-pending.json').exists())

    def test_pending_recovers_after_install_but_before_parameter_application(self):
        with patch.object(PluginManager, 'update_settings', side_effect=RuntimeError('simulated interruption')):
            with self.assertRaisesRegex(RuntimeError, 'simulated interruption'):
                launcher.initialize(self.data)
        self.assertFalse(self.config().plugin_flags[PID])
        self.assertEqual(launcher.read_json(self.data/'bundled-install-pending.json')['id'], PID)
        before = self.package_directories()
        launcher.initialize(self.data)
        cfg = self.config(); manager = PluginManager(cfg, self.data/'plugins')
        self.assertEqual(before, self.package_directories())
        self.assertTrue(cfg.plugin_flags[PID]); self.assertEqual(manager.settings(PID)['probe_value'], 7)
        self.assertEqual(self.marker()['ids'], [PID])
        self.assertFalse((self.data/'bundled-install-pending.json').exists())

    def test_pending_recovers_after_enable_before_completed_marker_is_saved(self):
        original = launcher.write_json
        def interrupted_write(path, value):
            if Path(path).name == 'bundled-plugins.json':
                raise RuntimeError('simulated marker interruption')
            return original(path, value)
        with patch.object(launcher, 'write_json', side_effect=interrupted_write):
            with self.assertRaisesRegex(RuntimeError, 'marker interruption'):
                launcher.initialize(self.data)
        self.assertTrue(self.config().plugin_flags[PID])
        self.assertTrue((self.data/'bundled-install-pending.json').exists())
        before = self.package_directories()
        launcher.initialize(self.data)
        self.assertEqual(before, self.package_directories())
        self.assertTrue(self.config().plugin_flags[PID])
        self.assertEqual(self.marker()['ids'], [PID])
        self.assertFalse((self.data/'bundled-install-pending.json').exists())

    def test_completed_plugin_disabled_by_user_stays_disabled(self):
        launcher.initialize(self.data)
        cfg = self.config(); cfg.plugin_flags[PID] = False
        cfg.plugin_settings[PID]['probe_value'] = 4; cfg.save()
        before = (self.data/'config.json').read_bytes()
        launcher.initialize(self.data)
        self.assertEqual(before, (self.data/'config.json').read_bytes())
        self.assertFalse(self.config().plugin_flags[PID])
        self.assertEqual(self.config().plugin_settings[PID]['probe_value'], 4)

    def test_removed_completed_plugin_is_not_reinstalled(self):
        launcher.initialize(self.data)
        async def remove():
            manager = PluginManager(self.config(), self.data/'plugins')
            try:
                await remove_package(manager, PID)
            finally:
                await manager.close()
        asyncio.run(remove())
        before = (self.data/'plugins/manifest.json').read_bytes()
        launcher.initialize(self.data)
        self.assertEqual(before, (self.data/'plugins/manifest.json').read_bytes())
        self.assertNotIn(PID, self.config().plugin_flags)
        self.assertFalse(any(i['id'] == PID for i in PluginManager(self.config(), self.data/'plugins').items))
        self.assertEqual(self.marker()['ids'], [PID])

    def test_preexisting_untracked_plugin_is_not_reenabled_or_reconfigured(self):
        self.write_pack([]); launcher.initialize(self.data)
        async def preinstall():
            manager = PluginManager(self.config(), self.data/'plugins')
            try:
                await install_package(manager, self.payload)
                manager.update_settings(PID, {'probe_value': 3})
            finally:
                await manager.close()
        asyncio.run(preinstall())
        self.assertFalse(self.config().plugin_flags[PID])
        self.write_pack([self.entry]); before = self.package_directories()
        launcher.initialize(self.data)
        self.assertEqual(before, self.package_directories())
        self.assertFalse(self.config().plugin_flags[PID])
        self.assertEqual(self.config().plugin_settings[PID]['probe_value'], 3)
        self.assertEqual(self.marker()['ids'], [PID])

    def test_incomplete_configuration_is_rejected_without_overwriting(self):
        self.data.mkdir()
        original = b'{"name":"Preserve fixture config"}'
        (self.data/'config.json').write_bytes(original)
        with self.assertRaisesRegex(RuntimeError, 'Incomplete instance'):
            launcher.initialize(self.data)
        self.assertEqual(original, (self.data/'config.json').read_bytes())
        self.assertFalse((self.data/'secrets.json').exists())
        self.assertFalse((self.data/'providers.json').exists())
        self.assertFalse((self.data/'bundled-plugins.json').exists())

    def test_configuration_and_plugin_state_survive_reinitialization(self):
        launcher.initialize(self.data)
        cfg = self.config(); cfg.update({'name':'Fixture custom name', 'persona':'Fixture custom persona'})
        state_dir = self.data/'plugins/state'/PID; state_dir.mkdir(parents=True)
        (state_dir/'notes.txt').write_text('Preserved fixture state', encoding='utf-8')
        names = ['config.json', 'secrets.json', 'connections.json', 'providers.json', 'resources/curated.json', 'plugins/state/'+PID+'/notes.txt']
        before = {name:(self.data/name).read_bytes() for name in names}
        launcher.initialize(self.data)
        self.assertEqual(before, {name:(self.data/name).read_bytes() for name in names})

    def test_bad_package_checksum_cannot_create_plugin_or_completion_marker(self):
        bad = {**self.entry, 'sha256':'0'*64}; self.write_pack([bad])
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            launcher.initialize(self.data)
        self.assertFalse((self.data/'plugins/manifest.json').exists())
        self.assertFalse((self.data/'bundled-plugins.json').exists())
        self.assertFalse((self.data/'bundled-install-pending.json').exists())

    def test_occupied_port_refuses_start_and_does_not_end_existing_service(self):
        with socket.socket() as occupied:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                occupied.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            occupied.bind(('127.0.0.1', 0)); occupied.listen(1); port = occupied.getsockname()[1]
            (self.root/'windows.json').write_text(json.dumps({'port':port}), encoding='utf-8')
            with patch.object(launcher.subprocess, 'Popen') as spawn, patch.object(launcher, 'open_chrome') as browser:
                with self.assertRaisesRegex(RuntimeError, 'occupied'):
                    launcher.start(self.data, no_browser=True)
            spawn.assert_not_called(); browser.assert_not_called()
            # The competing service is still listening, rather than merely having
            # a stale process/PID entry left behind.
            with socket.create_connection(('127.0.0.1', port), timeout=1):
                connection, _ = occupied.accept(); connection.close()

    def test_repeated_start_reuses_existing_instance_without_reinitializing(self):
        self.data.mkdir()
        existing = {'port':18801, 'instance':'already-running'}
        with patch.object(launcher, 'running', return_value=existing), patch.object(launcher, 'initialize') as initialize, patch.object(launcher.subprocess, 'Popen') as spawn, redirect_stdout(io.StringIO()):
            launcher.start(self.data, no_browser=True)
        initialize.assert_not_called(); spawn.assert_not_called()

    def test_new_server_uses_pythonw_and_create_no_window(self):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
        (self.root/'windows.json').write_text(json.dumps({'port':port}), encoding='utf-8')
        state = {'port':port, 'instance':'new-fixture-instance'}
        process = SimpleNamespace(poll=lambda:None)
        with patch.object(launcher, 'running', side_effect=[None, state]), patch.object(launcher.uuid, 'uuid4', return_value=SimpleNamespace(hex=state['instance'])), patch.object(launcher.subprocess, 'Popen', return_value=process) as spawn, redirect_stdout(io.StringIO()):
            launcher.start(self.data, no_browser=True)
        args, kwargs = spawn.call_args
        self.assertEqual(Path(args[0][0]).name.lower(), 'pythonw.exe')
        self.assertEqual(kwargs['creationflags'], subprocess.CREATE_NO_WINDOW)
        self.assertEqual(kwargs['cwd'], self.root)
        self.assertIn(str(self.data), args[0])

    def test_graceful_stop_waits_for_both_metadata_removal_and_real_lock_release(self):
        state = self.fixture_runtime()
        metadata = self.data/'runtime.json'
        waiting_metadata, waiting_lock, completed = threading.Event(), threading.Event(), threading.Event()
        errors = []; calls = []; output = io.StringIO(); original_sleep = time.sleep
        def fake_request(opener, port, path, payload=None, csrf=None):
            calls.append((path, csrf))
            if path == '/api/login': return {'ok':True, 'csrf_token':'fixture-csrf'}
            self.assertEqual(path, '/api/windows/shutdown'); self.assertEqual(csrf, 'fixture-csrf')
            return {'ok':True, 'data':{'stopping':True}}
        def observe_sleep(_):
            (waiting_metadata if metadata.exists() else waiting_lock).set()
            original_sleep(.01)
        def stopper():
            try: launcher.stop(self.data)
            except BaseException as exc: errors.append(exc)
            finally: completed.set()
        with patch.object(launcher, 'running', return_value=state), patch.object(launcher, 'client', return_value=object()), patch.object(launcher, 'request', side_effect=fake_request), patch.object(launcher.time, 'sleep', side_effect=observe_sleep), redirect_stdout(output):
            with launcher.lock(self.data/'server.lock'):
                thread = threading.Thread(target=stopper, daemon=True); thread.start()
                self.assertTrue(waiting_metadata.wait(2), 'stop never waited for runtime metadata')
                self.assertFalse(completed.is_set()); self.assertNotIn('Stopped gracefully', output.getvalue())
                # stop also holds the launcher lock, so a racing Start cannot
                # initialize or spawn while the old server is cleaning up.
                with self.assertRaises(RuntimeError):
                    with launcher.lock(self.data/'launcher.lock'): pass
                metadata.unlink()
                self.assertTrue(waiting_lock.wait(2), 'stop did not wait for server lock')
                self.assertFalse(completed.is_set()); self.assertNotIn('Stopped gracefully', output.getvalue())
            self.assertTrue(completed.wait(2), 'stop did not finish after lock release')
            thread.join(2)
        self.assertEqual(errors, [])
        self.assertIn('Stopped gracefully', output.getvalue())
        self.assertEqual(calls, [('/api/login',None),('/api/windows/shutdown','fixture-csrf')])
        with launcher.lock(self.data/'server.lock'): pass
        with launcher.lock(self.data/'launcher.lock'): pass

    def test_pending_shutdown_never_claims_success_or_forces_process_termination(self):
        state = self.fixture_runtime(); output = io.StringIO()
        def fake_request(opener, port, path, payload=None, csrf=None):
            return {'csrf_token':'fixture-csrf'} if path == '/api/login' else {'ok':True}
        with patch.object(launcher, 'running', return_value=state), patch.object(launcher, 'client', return_value=object()), patch.object(launcher, 'request', side_effect=fake_request), patch.object(launcher.time, 'sleep'), patch.object(launcher.subprocess, 'Popen') as spawn, redirect_stdout(output):
            with self.assertRaisesRegex(RuntimeError, 'still pending'):
                launcher.stop(self.data)
        spawn.assert_not_called(); self.assertNotIn('Stopped gracefully', output.getvalue())
        self.assertTrue((self.data/'runtime.json').exists())
        with launcher.lock(self.data/'launcher.lock'): pass

    def test_actual_windows_lock_excludes_another_handle_and_releases(self):
        path = self.data/'server.lock'
        with launcher.lock(path):
            with self.assertRaisesRegex(RuntimeError, 'Another launcher'):
                with launcher.lock(path): pass
        with launcher.lock(path): pass


if __name__ == '__main__': unittest.main()
