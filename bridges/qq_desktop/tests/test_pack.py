import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT=Path(__file__).resolve().parents[1]
s=importlib.util.spec_from_file_location('qq_bridge_source_builder',ROOT/'scripts/build_source.py');builder=importlib.util.module_from_spec(s);s.loader.exec_module(builder)


class SourceTests(unittest.TestCase):
    def test_fixed_allowlist_omits_runtime_credentials_and_binary_client(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)/'source';root.mkdir();(root/'LICENSE').write_text('test MIT')
            for name in builder.FILES:(root/name).write_text('public\r\n',encoding='utf-8')
            for folder in ('qq_desktop','scripts','tests'):
                (root/folder).mkdir();(root/folder/'public.py').write_text('pass\n')
            (root/'data').mkdir();(root/'data/secrets.json').write_text('private-secret-marker')
            (root/'private.env').write_text('private-secret-marker');(root/'qq.deb').write_bytes(b'not-redistributable')
            report=builder.build(root,Path(t)/'out')
            with zipfile.ZipFile(report['archive']) as z:
                self.assertFalse(any('/data/' in n or n.endswith(('.env','.deb')) for n in z.namelist()))
                self.assertNotIn(b'private-secret-marker',b''.join(z.read(n) for n in z.namelist()))
                m=json.loads(z.read('YukinoQQDesktop-'+builder.VERSION+'/source-manifest.json'));self.assertFalse(m['qq_client_included'])
            with self.assertRaises(FileExistsError):builder.build(root,Path(t)/'out')


if __name__=='__main__':unittest.main()
