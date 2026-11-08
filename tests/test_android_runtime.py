"""Android capability boundaries; desktop remains fully featured."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from ig import config, dash, views

BOOTSTRAP = Path(__file__).resolve().parents[1] / 'android/app/src/main/python/ig_android.py'


class AndroidRuntimeTests(unittest.TestCase):
    def test_disabled_features_do_not_fetch_media(self):
        with patch.object(config, 'ANDROID', True), patch('ig.dash.plan') as plan:
            self.assertIsNone(dash.assemble('abcde', '<MPD/>'))
            plan.assert_not_called()

    def test_android_flags_are_explicit_in_boot_data(self):
        with patch.object(config, 'ANDROID', True), patch('ig.views.load_feeds', return_value={}), \
                patch('ig.views.feed_accounts', return_value=set()), patch('ig.views.blocked_accounts', return_value=set()), \
                patch('ig.views.selected_feeds', return_value=None):
            html = views.reels_html()
            self.assertIn('"android": true', html)
            self.assertNotIn('song_recognition', html)

    @unittest.skipUnless(BOOTSTRAP.exists(), 'Android source is not in this distribution')
    def test_private_backend_requires_cookie_and_matching_origin(self):
        spec = importlib.util.spec_from_file_location('ig_android_test', BOOTSTRAP)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        access = module.AppAccess('secret')
        handler = Mock()
        handler.server.server_port = 1234
        handler.headers = {'Host':'127.0.0.1:1234'}
        self.assertFalse(access.allow(handler))
        handler.headers['Cookie'] = 'ig_android=secret'
        self.assertTrue(access.allow(handler))
        handler.headers['Origin'] = 'https://example.test'
        self.assertFalse(access.allow(handler))
        handler.headers['Origin'] = 'http://127.0.0.1:1234'
        self.assertTrue(access.allow(handler))
        handler.headers['Host'] = 'example.test'
        self.assertFalse(access.allow(handler))
