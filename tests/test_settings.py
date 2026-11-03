import io
import json
from unittest.mock import patch

from ig import config, views
from ig.server import Handler
from test_ig import ProjectTests


class SettingsTests(ProjectTests):
    def request(self, path, data=None, allowed=True):
        handler = Handler.__new__(Handler)
        handler.path = path
        raw = json.dumps(data).encode()
        handler.headers = {'Content-Length': str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        with patch.object(handler, '_send') as send, patch.object(handler, '_allowed', return_value=allowed):
            if data is None:
                handler.do_GET()
            else:
                handler.do_POST()
        return send.call_args.args if send.called else None

    def test_scroll_preference_persists_without_changing_other_data(self):
        config.save_feeds({'keep': ['old']})
        config.set_selected_feeds(['keep'])
        config.set_blocked('blocked', True)
        self.assertEqual(config.scroll_animation(), 'snappy')
        response = self.request('/settings/scroll', {'mode': 'smooth'})
        self.assertEqual(response[0], 200)
        self.assertEqual(json.loads(response[1]), {'scroll_animation': 'smooth'})
        self.assertEqual(config.scroll_animation(), 'smooth')
        data = json.loads(self.request('/settings/data')[1])
        self.assertEqual(data, {'feeds': {'keep': ['old']}, 'scroll_animation': 'smooth'})
        self.assertEqual(config.selected_feeds(), ['keep'])
        self.assertEqual(config.blocked_accounts(), {'blocked'})
        self.assertIn('"scroll_animation": "smooth"', views.reels_html())

    def test_invalid_scroll_preference_does_not_write(self):
        config.set_scroll_animation('snappy')
        for mode in [None, 'fast', True, [], 1]:
            self.assertEqual(self.request('/settings/scroll', {'mode': mode})[0], 400)
            self.assertEqual(config.scroll_animation(), 'snappy')

    def test_remove_only_requested_membership_and_fresh_counts(self):
        config.save_feeds({'one': ['shared', 'keep'], 'two': ['shared']})
        config.set_selected_feeds(['one'])
        config.set_blocked('blocked', True)
        response = self.request('/feeds/accounts/remove', {'name': 'one', 'username': 'SHARED'})
        self.assertEqual(response[0], 200)
        self.assertEqual(json.loads(response[1]), {'name': 'one', 'username': 'shared', 'removed': True,
                                                  'accounts': ['keep'], 'count': 1})
        self.assertEqual(config.load_feeds(), {'one': ['keep'], 'two': ['shared']})
        self.assertEqual(config.selected_feeds(), ['one'])
        self.assertEqual(config.blocked_accounts(), {'blocked'})
        self.assertEqual(json.loads(self.request('/settings/data')[1])['feeds']['one'], ['keep'])
        repeated = self.request('/feeds/accounts/remove', {'name': 'one', 'username': 'shared'})
        self.assertFalse(json.loads(repeated[1])['removed'])

    def test_invalid_membership_removal_does_not_write(self):
        config.save_feeds({'one': ['keep']})
        for body, status in [({'name': 'missing', 'username': 'keep'}, 404),
                             ({'name': 'one', 'username': 'bad/name'}, 400),
                             ({'name': '../one', 'username': 'keep'}, 400),
                             ({'name': 'one'}, 400)]:
            self.assertEqual(self.request('/feeds/accounts/remove', body)[0], status)
            self.assertEqual(config.load_feeds(), {'one': ['keep']})

    def test_settings_boot_has_android_flag_and_auth_applies(self):
        with patch.object(views, '_template', return_value='{{boot}}'), patch.object(config, 'ANDROID', True):
            self.assertEqual(json.loads(views.settings_html()), {'android': True, 'scroll_animation': 'snappy'})
        for path, body in [('/settings', None), ('/settings/data', None),
                           ('/settings/scroll', {'mode': 'snappy'}),
                           ('/feeds/accounts/remove', {'name': 'one', 'username': 'keep'})]:
            self.assertIsNone(self.request(path, body, allowed=False))
