import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from yukino_core.banter_bank import load_bank, merge_banter
from yukino_core.notice_plugins import validate_notice_setting

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location('copy_importer', ROOT / 'scripts/import_banter.py')
importer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(importer)


class BankTests(unittest.TestCase):
    def test_original_bank_and_new_notice_limit(self):
        bank = load_bank()
        self.assertEqual(len(bank['poke']), 60)
        self.assertEqual(sum(map(len, bank['smalltalk'].values())), 72)
        validate_notice_setting('templates', '\n'.join(bank['poke']))
        validate_notice_setting('templates', '\n'.join('第' + str(i) + '句' for i in range(100)))
        with self.assertRaises(ValueError):
            validate_notice_setting('templates', '\n'.join(str(i) for i in range(101)))

    def test_append_idempotence_preserves_custom_and_keyword(self):
        config = {'persona': 'existing', 'other': {'keep': True}, 'plugin_settings': {
            'poke': {'templates': '我的自定义', 'probability': .5},
            'keyword_reply': {'rules_json': '[{"key":"在吗","reply":"旧回复","mode":"exact"}]', 'cooldown_seconds': 15}}}
        before = copy.deepcopy(config)
        merged, proof = merge_banter(config, load_bank(), include_keywords=True)
        self.assertEqual(config, before)
        self.assertEqual(merged['persona'], 'existing')
        self.assertEqual(merged['other'], config['other'])
        self.assertEqual(merged['plugin_settings']['poke']['probability'], .5)
        self.assertEqual(proof['poke_total'], 61)
        self.assertEqual(json.loads(merged['plugin_settings']['keyword_reply']['rules_json'])[0]['reply'], '旧回复')
        repeated, proof = merge_banter(merged, load_bank(), include_keywords=True)
        self.assertEqual(repeated, merged)
        self.assertEqual((proof['poke_added'], proof['keyword_added']), (0, 0))

    def test_overflow_rejects_without_mutating_input(self):
        config = {'plugin_settings': {'poke': {'templates': '\n'.join('custom' + str(i) for i in range(41))}}}
        before = copy.deepcopy(config)
        with self.assertRaises(ValueError):
            merge_banter(config, load_bank())
        self.assertEqual(config, before)

    def test_disk_import_preview_backup_and_running_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            old = b'{"persona":"private role","plugin_settings":{}}\n'
            (data / 'config.json').write_bytes(old)
            preview = importer.import_copy(data, include_keywords=True, dry_run=True)
            self.assertEqual(preview['poke_total'], 60)
            self.assertEqual((data / 'config.json').read_bytes(), old)
            self.assertFalse((data / 'backups').exists())
            proof = importer.import_copy(data, include_keywords=True)
            self.assertEqual(Path(proof['backup']).read_bytes(), old)
            self.assertEqual(json.loads((data / 'config.json').read_text(encoding='utf-8'))['persona'], 'private role')
            (data / 'runtime.json').write_text('{}')
            after = (data / 'config.json').read_bytes()
            with self.assertRaises(ValueError):
                importer.import_copy(data)
            self.assertEqual((data / 'config.json').read_bytes(), after)
