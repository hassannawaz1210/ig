import io
import json
import subprocess
import sys
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
        self.assertEqual(config.scroll_animation(), 'smooth', 'smooth transitions are the default')
        response = self.request('/settings/scroll', {'mode': 'snappy'})
        self.assertEqual(response[0], 200)
        self.assertEqual(json.loads(response[1]), {'scroll_animation': 'snappy'})
        self.assertEqual(config.scroll_animation(), 'snappy')
        data = json.loads(self.request('/settings/data')[1])
        self.assertEqual(data, {'feeds': {'keep': ['old']}, 'scroll_animation': 'snappy',
                                'selected_feeds': ['keep'], 'blocked_accounts': ['blocked'],
                                'sound': False, 'auto_scroll': False,
                                'playback_missing': ['sound', 'auto_scroll']})
        self.assertEqual(config.selected_feeds(), ['keep'])
        self.assertEqual(config.blocked_accounts(), {'blocked'})
        self.assertIn('"scroll_animation": "snappy"', views.reels_html())

    def test_playback_preferences_partial_updates_and_profile_preservation(self):
        config.save_feeds({'keep': ['old']})
        config.set_selected_feeds(['keep'])
        config.set_blocked('blocked', True)
        config.set_scroll_animation('smooth')
        self.assertEqual(config.playback_preferences(), {'sound': False, 'auto_scroll': False})
        response = self.request('/settings/playback', {'sound': True})
        self.assertEqual(response[0], 200)
        self.assertEqual(json.loads(response[1]), {'sound': True, 'auto_scroll': False})
        response = self.request('/settings/playback', {'auto_scroll': True})
        self.assertEqual(json.loads(response[1]), {'sound': True, 'auto_scroll': True})
        response = self.request('/settings/playback', {'sound': False})
        self.assertEqual(json.loads(response[1]), {'sound': False, 'auto_scroll': True})
        data = json.loads(self.request('/settings/data')[1])
        self.assertEqual((data['sound'], data['auto_scroll']), (False, True))
        self.assertEqual(data['feeds'], {'keep': ['old']})
        self.assertEqual(config.selected_feeds(), ['keep'])
        self.assertEqual(config.scroll_animation(), 'smooth')
        self.assertEqual(config.blocked_accounts(), {'blocked'})
        html = views.reels_html()
        self.assertIn('"sound": false', html)
        self.assertIn('"auto_scroll": true', html)

    def test_legacy_playback_migration_only_inserts_missing_preferences(self):
        state = config.playback_preferences(include_missing=True)
        self.assertEqual(state, {'sound': False, 'auto_scroll': False,
                                 'playback_missing': ['sound', 'auto_scroll']})
        self.assertEqual(config.playback_preferences(include_missing=True), state)
        self.assertIn('"playback_missing": ["sound", "auto_scroll"]', views.reels_html())
        # Another client saves false after the first client's boot snapshot.
        config.set_playback_preferences({'sound': False})
        response = self.request('/settings/playback', {'migrate': True, 'sound': True, 'auto_scroll': True})
        self.assertEqual(response[0], 200)
        self.assertEqual(json.loads(response[1]), {'sound': False, 'auto_scroll': True})
        self.assertEqual(config.playback_preferences(include_missing=True)['playback_missing'], [])
        self.assertEqual(json.loads(self.request('/settings/data')[1])['playback_missing'], [])
        # Retrying migration is harmless, while an explicit toggle still updates it.
        self.request('/settings/playback', {'migrate': True, 'sound': True, 'auto_scroll': False})
        self.assertEqual(config.playback_preferences(), {'sound': False, 'auto_scroll': True})
        self.request('/settings/playback', {'sound': True})
        self.assertTrue(config.playback_preferences()['sound'])

    def test_invalid_playback_preferences_do_not_partially_write(self):
        config.set_playback_preferences({'sound': True, 'auto_scroll': True})
        for changes in [{}, {'sound': 1}, {'sound': 'false'}, {'auto_scroll': None},
                        {'sound': False, 'unexpected': True}, {'mode': 'smooth'},
                        {'migrate': True}, {'migrate': 'true', 'sound': False}, []]:
            self.assertEqual(self.request('/settings/playback', changes)[0], 400)
            self.assertEqual(config.playback_preferences(), {'sound': True, 'auto_scroll': True})

    def test_playback_and_feed_selection_survive_new_process(self):
        config.save_feeds({'one': ['first'], 'two': ['second']})
        config.set_selected_feeds(['two', 'one'])
        config.set_playback_preferences({'sound': True, 'auto_scroll': True})
        config.set_scroll_animation('smooth')
        script = """
import json, sys
from pathlib import Path
from ig import config
config.CONFIG_DIR = Path(sys.argv[1])
print(json.dumps({'playback': config.playback_preferences(),
                  'selection': config.selected_feeds(), 'scroll': config.scroll_animation()}))
"""
        result = subprocess.run([sys.executable, '-c', script, str(config.CONFIG_DIR)],
                                check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout), {'playback': {'sound': True, 'auto_scroll': True},
                         'selection': ['one', 'two'], 'scroll': 'smooth'})
        config.set_selected_feeds(None)
        self.assertIsNone(config.selected_feeds())

    def test_settings_data_exposes_fresh_feed_selection_and_blocks(self):
        config.save_feeds({'one': ['first'], 'two': ['second']})
        first = json.loads(self.request('/settings/data')[1])
        self.assertIsNone(first['selected_feeds'])
        self.assertEqual(first['blocked_accounts'], [])
        config.set_selected_feeds(['two'])
        config.set_blocked('z_blocked', True)
        config.set_blocked('a_blocked', True)
        second = json.loads(self.request('/settings/data')[1])
        self.assertEqual(second['selected_feeds'], ['two'])
        self.assertEqual(second['blocked_accounts'], ['a_blocked', 'z_blocked'])
        config.set_selected_feeds(None)
        config.set_blocked('a_blocked', False)
        third = json.loads(self.request('/settings/data')[1])
        self.assertIsNone(third['selected_feeds'])
        self.assertEqual(third['blocked_accounts'], ['z_blocked'])

    def test_app_version_falls_back_to_the_package_outside_android(self):
        with patch.dict('os.environ', {'IG_APP_VERSION': '', 'IG_APP_BUILD': ''}):
            info = config.app_version()
        self.assertEqual(info['build'], '')
        self.assertTrue(info['version'], 'desktop shows the installed package version')

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
            with patch.dict('os.environ', {'IG_APP_VERSION': '1.2.3', 'IG_APP_BUILD': '45'}):
                self.assertEqual(json.loads(views.settings_html()), {
                    'android': True, 'scroll_animation': 'smooth', 'version': '1.2.3', 'build': '45',
                    'repository': 'https://github.com/hassannawaz1210/ig'})
            self.assertEqual(json.loads(views.feeds_html()), {'android': True})
            boot = json.loads(views.viewer_html('@someone', 'posts', [], username='someone'))
            self.assertTrue(boot['android'], 'profile page gets the phone layout in the app')
        page = self.request('/settings/feeds')
        self.assertEqual((page[0], page[2]), (200, 'text/html; charset=utf-8'))
        self.assertIn(b'id="settings-feeds"', page[1])
        for path, body in [('/settings', None), ('/settings/feeds', None), ('/settings/data', None),
                           ('/settings/scroll', {'mode': 'snappy'}),
                           ('/settings/playback', {'sound': True}),
                           ('/feeds/accounts/remove', {'name': 'one', 'username': 'keep'})]:
            self.assertIsNone(self.request(path, body, allowed=False))
