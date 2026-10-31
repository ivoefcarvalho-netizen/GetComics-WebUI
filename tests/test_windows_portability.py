import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import getcomics_web_ui.webui as w

class WindowsPortabilityTests(unittest.TestCase):
    def test_html_has_all_settings(self):
        html=(ROOT/'getcomics_web_ui'/'index.html').read_text(encoding='utf-8')
        for field in ('id="connections"', 'id="max-pages"', 'id="pixeldrain-key"',
                      'id="toggle-pause"', 'id="destination-input"', 'id="article-url"',
                      'id="tag"', 'id="min-year"'):
            self.assertIn(field,html)
    def test_private_settings_and_cache_outside_app(self):
        self.assertTrue(w.CACHE_DIR.is_relative_to(w.CONFIG_DIR))
        self.assertTrue(w.KEY_FILE.is_relative_to(w.CONFIG_DIR))
    def test_windows_secret_not_readable_elsewhere(self):
        if os.name != 'nt':
            from windows_secret import protect
            with self.assertRaises(OSError): protect(b'example')
    @unittest.skipUnless(os.name == 'nt','Windows apenas')
    def test_dpapi_roundtrip(self):
        from windows_secret import protect, unprotect
        sample=b'private-test-key'
        encrypted=protect(sample)
        self.assertNotIn(sample, encrypted)
        self.assertEqual(unprotect(encrypted), sample)
