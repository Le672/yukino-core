import unittest
from yukino_core.config import Config, DEFAULTS, settings_schema


class SearchSettingsTests(unittest.TestCase):
    def test_settings_are_complete_and_opt_in(self):
        keys={key for key in DEFAULTS if key.startswith('web_search_')}
        fields={row['key']:row for row in settings_schema()['fields']}
        self.assertEqual(len(keys),9)
        self.assertFalse(DEFAULTS['web_search_enabled'])
        self.assertTrue(all(fields[key]['section']=='web_search' for key in keys))
        self.assertEqual(Config.validate({key:DEFAULTS[key] for key in keys}),{key:DEFAULTS[key] for key in keys})

    def test_endpoint_and_limits_cannot_smuggle_credentials_or_private_targets(self):
        for url in ('https://127.0.0.1/search','https://10.0.0.1/search','http://example.com/search',
                    'https://user:pass@example.com/search','https://example.com/search?token=secret',
                    'https://localhost/search','https://example.com:8080/search'):
            with self.subTest(url=url),self.assertRaises(ValueError):Config.validate({'web_search_searxng_url':url})
        for patch in ({'web_search_results':9},{'web_search_read_pages':4},{'web_search_timeout_seconds':0},
                      {'web_search_enabled':'true'},{'web_search_cache_seconds':True}):
            with self.subTest(patch=patch),self.assertRaises(ValueError):Config.validate(patch)
