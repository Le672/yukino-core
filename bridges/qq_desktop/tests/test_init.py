import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
s=importlib.util.spec_from_file_location('qq_bridge_initializer',ROOT/'scripts/init_instance.py');initializer=importlib.util.module_from_spec(s);s.loader.exec_module(initializer)


class InitializationTests(unittest.TestCase):
    def test_disabled_configuration_private_token_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as t,patch.object(initializer.os,'chown',create=True) as chown,patch.object(initializer.os,'geteuid',return_value=0,create=True):
            root=Path(t)/'instance';result=initializer.initialize(root)
            cfg=json.loads((root/'data/config.json').read_text());token=(root/'private.env').read_text()
            self.assertFalse(cfg['enabled']);self.assertEqual(cfg['bindings'],[]);self.assertEqual(cfg['app_name'],'qq')
            self.assertIn('QQ_DESKTOP_TOKEN=',token);self.assertGreaterEqual(len(token.split('=',1)[1].strip()),32)
            self.assertNotIn(token.strip(),json.dumps(result));self.assertFalse(result['private_token_printed'])
            previous={p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}
            with self.assertRaises(FileExistsError):initializer.initialize(root)
            self.assertEqual(previous,{p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()})
    def test_linux_nonroot_requires_explicit_initialization_permissions(self):
        with tempfile.TemporaryDirectory() as t,patch.object(initializer,'os',SimpleNamespace(name='posix',geteuid=lambda:1000)):
            with self.assertRaises(PermissionError):initializer.initialize(Path(t)/'instance')
            self.assertFalse((Path(t)/'instance').exists())


if __name__=='__main__':unittest.main()
