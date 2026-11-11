import io
import json
from unittest.mock import patch

from ig import backup, config, recommendations
from ig.server import Handler
from test_ig import ProjectTests


def payload(feeds=None, blocked=None, **extra):
    return {'format': 'ig-feeds', 'version': 1, 'feeds': feeds or {}, 'blocked': blocked or [], **extra}


class BackupTests(ProjectTests):
    def request(self, data=None, path='/feeds/import', allowed=True, size=None):
        handler = Handler.__new__(Handler)
        handler.path = path
        data = data if isinstance(data, bytes) else json.dumps(data).encode()
        handler.headers = {'Content-Length': str(len(data) if size is None else size)}
        handler.rfile = io.BytesIO(data)
        with patch.object(handler, '_send') as send, patch.object(handler, '_allowed', return_value=allowed):
            if path.split('?')[0] == '/feeds/export':
                handler.do_GET()
            else:
                handler.do_POST()
        return send.call_args.args if send.called else None

    def test_selective_export_and_import_preserves_topics_and_selection(self):
        config.save_feeds({'edits': ['one'], 'memes': ['two']})
        config.set_selected_feeds(['memes'])
        document = json.loads(backup.export())
        document['feed_topics'] = {'edits': ['movie edits'], 'memes': ['funny memes']}
        selected = backup.select_feeds(document, ['edits'])
        self.assertEqual(selected['feeds'], {'edits': ['one']})
        self.assertEqual(selected['feed_topics'], {'edits': ['movie edits']})
        self.assertNotIn('selected_feeds', selected['settings'])
        self.assertEqual(document['settings']['selected_feeds'], ['memes'])
        response = self.request(path='/feeds/export?feeds=%5B%22edits%22%5D')
        self.assertEqual(response[0], 200)
        self.assertEqual(json.loads(response[1])['feeds'], {'edits': ['one']})
        config.save_feeds({'existing': ['local']})
        config.set_selected_feeds(['existing'])
        response = self.request(document, '/feeds/import?feeds=%5B%22edits%22%5D')
        self.assertEqual(response[0], 200)
        self.assertEqual(config.load_feeds(), {'existing': ['local'], 'edits': ['one']})
        self.assertEqual(config.selected_feeds(), ['existing'])
        self.assertEqual(config.feed_topic_words().get('edits'), ['movie edits'])

    def test_selection_rejects_unknown_feeds_and_supports_no_feeds(self):
        config.save_feeds({'edits': ['one']})
        for value in ['%5B%22missing%22%5D', '%22edits%22', 'bad']:
            self.assertEqual(self.request(path='/feeds/export?feeds=' + value)[0], 400)
        self.assertEqual(json.loads(self.request(path='/feeds/export?feeds=%5B%5D')[1])['feeds'], {})

    def test_export_round_trip_includes_portable_data_only(self):
        config.save_feeds({'edits': ['one', 'two'], 'empty': []})
        config.set_blocked('blocked', True)
        config.set_selected_feeds(['edits'])
        config.set_prefer_memes(True)
        response = self.request(path='/feeds/export')
        self.assertEqual(response[0], 200)
        self.assertIn('attachment; filename="ig-feeds-', response[3]['Content-Disposition'])
        document = json.loads(response[1])
        self.assertEqual(document, payload({'edits': ['one', 'two'], 'empty': []}, ['blocked'],
            version=2, saved_reels=[], settings={'sound':False, 'auto_scroll':False,
                'scroll_animation':'smooth', 'selected_feeds':['edits']}, feed_topics={}))
        self.assertEqual(backup.parse(response[1]), document)

    def test_preview_does_not_write_and_counts_memberships(self):
        config.save_feeds({'existing': ['keep']})
        response = self.request(payload({'one': ['same'], 'two': ['same', 'other']}, ['bad']),
                                '/feeds/import/preview')
        self.assertEqual(response[0], 200)
        self.assertEqual(json.loads(response[1]), {'feeds': 2, 'accounts': 3, 'blocked': 1,
            'names': ['one', 'two'], 'saved_reels':0, 'settings':[]})
        self.assertEqual(config.load_feeds(), {'existing': ['keep']})
        self.assertEqual(config.blocked_accounts(), set())

    def test_merge_preserves_all_data_selection_and_existing_blocks(self):
        config.save_feeds({'keep': ['old'], 'edits': ['one']})
        config.set_selected_feeds(['keep'])
        config.set_blocked('blocked', True)
        document = payload({'edits': ['ONE', 'two'], 'new': ['two', 'bad'], 'empty': []}, ['bad'])
        response = self.request(document)
        self.assertEqual(response[0], 200)
        result = json.loads(response[1])
        self.assertEqual((result['feeds'], result['added'], result['blocked']), (3, 3, 1))
        self.assertEqual(result['feed_accounts'], ['old', 'one', 'two', 'bad'])
        self.assertEqual(result['blocked_accounts'], ['bad', 'blocked'])
        self.assertEqual(config.load_feeds(), {'keep': ['old'], 'edits': ['one', 'two'], 'new': ['two', 'bad'], 'empty': []})
        self.assertEqual(config.selected_feeds(), ['keep'])
        self.assertEqual(config.blocked_accounts(), {'blocked', 'bad'})
        again = json.loads(self.request(document)[1])
        self.assertEqual((again['added'], again['blocked']), (0, 0))

    def test_bad_version_schema_and_limits_never_partially_write(self):
        config.save_feeds({'keep': ['old']})
        documents = [payload(version=3), payload(version=True), payload(format='other'),
                     payload({'new': ['valid'], 'bad/name': []}),
                     payload({'new': ['valid', 'bad/name']}), payload(blocked=['bad/name'])]
        for document in documents:
            with self.subTest(document=str(document)[:100]):
                self.assertIn(self.request(document)[0], (400, 413))
                self.assertEqual(config.load_feeds(), {'keep': ['old']})
        self.assertEqual(self.request(b'{broken')[0], 400)
        self.assertEqual(self.request(b'', size=backup.MAX_BYTES + 1)[0], 413)
        self.assertEqual(self.request(b'{}', size=10)[0], 400)

    def test_accumulated_accounts_round_trip_beyond_following_import_limit(self):
        for start in (0, 4000):
            document = payload({'collected': ['account' + str(n) for n in range(start, start + 4000)]})
            self.assertEqual(self.request(document)[0], 200)
        exported = self.request(path='/feeds/export')
        self.assertEqual(exported[0], 200)
        self.assertEqual(len(backup.parse(exported[1])['feeds']['collected']), 8000)
        config.save_feeds({})
        self.assertEqual(self.request(exported[1])[0], 200)
        self.assertEqual(len(config.load_feeds()['collected']), 8000)

    def test_backup_uses_byte_limit_not_arbitrary_feed_or_membership_limits(self):
        feeds = {str(n): [] for n in range(501)}
        feeds['large'] = ['account' + str(n) for n in range(50001)]
        data = json.dumps(payload(feeds)).encode()
        self.assertLess(len(data), backup.MAX_BYTES)
        self.assertEqual(backup.parse(data)['feeds'], feeds)

    def test_oversized_export_is_error_not_an_unusable_download(self):
        config.save_feeds({'keep': ['old']})
        with patch.object(backup, 'MAX_BYTES', 10):
            response = self.request(path='/feeds/export')
        self.assertEqual(response[0], 413)
        self.assertIn('No backup was created', json.loads(response[1])['error'])
        self.assertEqual(len(response), 3)
        self.assertEqual(config.load_feeds(), {'keep': ['old']})

    def test_write_failure_rolls_back_all_changes(self):
        config.save_feeds({'keep': ['old']})
        original = config._write_feeds
        def fail_after_write(db, feeds):
            original(db, feeds)
            raise RuntimeError('storage error')
        with patch.object(config, '_write_feeds', side_effect=fail_after_write):
            self.assertEqual(self.request(payload({'new': ['account']}, ['blocked']))[0], 500)
        self.assertEqual(config.load_feeds(), {'keep': ['old']})
        self.assertEqual(config.blocked_accounts(), set())

    def test_access_is_required_for_all_routes(self):
        for path in ['/feeds/export', '/feeds/import', '/feeds/import/preview']:
            self.assertIsNone(self.request(payload(), path, allowed=False))

    def saved(self, code='clip001', **extra):
        return {'shortcode':code, 'owner':'creator', 'caption':'A saved reel', 'taken_at':1700000000,
                'like_count':10, 'comment_count':2, 'saved_at':1700000100.5, **extra}

    def test_v2_export_saved_metadata_and_settings_round_trip(self):
        config.save_feeds({'mine':['creator']})
        config.set_selected_feeds(['mine'])
        config.set_playback_preferences({'sound':True, 'auto_scroll':True})
        config.set_scroll_animation('smooth')
        recommendations.remember_reels([self.saved(video='https://cdn.example/expiring.mp4')])
        recommendations.set_reel_saved('clip001', True, {'image':'https://cdn.example/expiring.jpg'})
        with recommendations.database() as db:
            db.execute('UPDATE saved_reels SET saved_at=123.5 WHERE code=?', ('clip001',))
            db.execute('INSERT INTO feedback VALUES (?, ?, ?)', ('clip001', 'like', 123))
        exported = self.request(path='/feeds/export')
        self.assertEqual(exported[0], 200)
        document = json.loads(exported[1])
        self.assertEqual(document['version'], 2)
        self.assertEqual(set(document), {'format','version','feeds','blocked','saved_reels','settings','feed_topics'})
        self.assertEqual(set(document['saved_reels'][0]), set(backup.SAVED_FIELDS))
        self.assertEqual(document['saved_reels'][0]['saved_at'], 123.5)
        self.assertNotIn(b'cdn.example', exported[1])
        self.assertNotIn(b'feedback', exported[1])
        self.assertEqual(document['settings'], {'sound':True,'auto_scroll':True,
            'scroll_animation':'smooth','selected_feeds':['mine']})
        recommendations.set_reel_saved('clip001', False)
        config.set_playback_preferences({'sound':False,'auto_scroll':False})
        config.set_scroll_animation('snappy')
        config.set_selected_feeds(None)
        response = self.request(exported[1])
        self.assertEqual(response[0], 200)
        result = json.loads(response[1])
        self.assertEqual(result['saved_reels_added'], 1)
        self.assertEqual(set(result['settings_applied']), backup.SETTING_KEYS)
        [saved] = recommendations.saved_reels()
        self.assertEqual((saved['shortcode'],saved['saved_at'],saved['url'],saved['image']),
            ('clip001',123.5,'https://www.instagram.com/reel/clip001/',None))
        self.assertEqual(config.playback_preferences(), {'sound':True,'auto_scroll':True})
        self.assertEqual(config.scroll_animation(), 'smooth')
        self.assertEqual(config.selected_feeds(), ['mine'])
        self.assertEqual(recommendations.feedback_for(['clip001']), {'clip001':'like'})

    def test_v2_merge_keeps_existing_saved_reels_and_explicit_false_null(self):
        config.save_feeds({'mine':['creator']})
        config.set_selected_feeds(['mine'])
        config.set_playback_preferences({'sound':True,'auto_scroll':True})
        config.set_scroll_animation('smooth')
        recommendations.remember_reels([self.saved()])
        recommendations.set_reel_saved('clip001', True, {'caption':'Keep this caption'})
        original = recommendations.saved_reels()[0]
        document = payload({'new':['new_creator']}, version=2,
            saved_reels=[self.saved(caption='Do not replace'), self.saved('clip002'), self.saved('clip002')],
            settings={'sound':False,'auto_scroll':False,'scroll_animation':'snappy','selected_feeds':None})
        result = self.request(document)
        self.assertEqual(result[0], 200)
        self.assertEqual(json.loads(result[1])['saved_reels_added'], 1)
        saved = {r['shortcode']:r for r in recommendations.saved_reels()}
        self.assertEqual(saved['clip001'], original)
        self.assertEqual(set(saved), {'clip001','clip002'})
        self.assertEqual(config.playback_preferences(), {'sound':False,'auto_scroll':False})
        self.assertEqual(config.scroll_animation(), 'snappy')
        self.assertIsNone(config.selected_feeds())
        self.assertEqual(json.loads(self.request(document)[1])['saved_reels_added'], 0)

    def test_v1_and_partial_v2_leave_unsupplied_settings_and_saved_data(self):
        config.save_feeds({'mine':['creator']})
        config.set_selected_feeds(['mine'])
        config.set_playback_preferences({'sound':True,'auto_scroll':True})
        config.set_scroll_animation('smooth')
        recommendations.remember_reels([self.saved()])
        recommendations.set_reel_saved('clip001', True)
        self.assertEqual(self.request(payload({'new':[]}))[0], 200)
        self.assertEqual(self.request(payload(version=2,settings={'sound':False}))[0], 200)
        self.assertEqual(config.playback_preferences(), {'sound':False,'auto_scroll':True})
        self.assertEqual(config.scroll_animation(), 'smooth')
        self.assertEqual(config.selected_feeds(), ['mine'])
        self.assertEqual(len(recommendations.saved_reels()), 1)
        self.assertEqual(self.request(payload(version=2,settings={'selected_feeds':[]}))[0], 200)
        self.assertEqual(config.selected_feeds(), [])
        self.assertEqual(backup.parse(backup.export())['settings']['selected_feeds'], [])

    def test_v2_invalid_saved_metadata_and_settings_never_write(self):
        config.save_feeds({'keep':['old']})
        invalid = [
            {'saved_reels':{}}, {'saved_reels':[self.saved(shortcode='../bad')]},
            {'saved_reels':[self.saved(owner='bad/name')]}, {'saved_reels':[self.saved(caption=[])]},
            {'saved_reels':[self.saved(video='https://cdn.example/expiring.mp4')]},
            {'saved_reels':[self.saved(like_count=True)]}, {'saved_reels':[self.saved(taken_at=-1)]},
            {'saved_reels':[self.saved(comment_count=2**63)]},
            {'saved_reels':[self.saved(saved_at=float('nan'))]},
            {'saved_reels':[self.saved(saved_at=float('inf'))]},
            {'saved_reels':[self.saved(saved_at=10**400)]},
            {'settings':[]}, {'settings':{'sound':1}}, {'settings':{'auto_scroll':'false'}},
            {'settings':{'scroll_animation':'fast'}}, {'settings':{'selected_feeds':'keep'}},
            {'settings':{'selected_feeds':['missing']}}, {'settings':{'selected_feeds':['bad/name']}},
            {'settings':{'watch_history':True}},
        ]
        for extra in invalid:
            with self.subTest(extra=str(extra)[:90]):
                response = self.request(payload({'new':['new_user']}, ['blocked'],version=2,**extra))
                self.assertEqual(response[0], 400)
                self.assertEqual(config.load_feeds(), {'keep':['old']})
                self.assertEqual(config.blocked_accounts(), set())
                self.assertEqual(recommendations.saved_reels(), [])
                self.assertEqual(config.playback_preferences(), {'sound':False,'auto_scroll':False})

    def test_v2_preview_is_read_only_and_lists_settings_that_will_apply(self):
        document = payload({'new':['creator']}, ['blocked'],version=2,
            saved_reels=[self.saved()], settings={'sound':False,'selected_feeds':None})
        result = self.request(document, '/feeds/import/preview')
        self.assertEqual(result[0], 200)
        self.assertEqual(json.loads(result[1])['saved_reels'], 1)
        self.assertEqual(json.loads(result[1])['settings'], ['sound','selected_feeds'])
        self.assertEqual(config.load_feeds(), {})
        self.assertEqual(config.blocked_accounts(), set())
        self.assertEqual(recommendations.saved_reels(), [])

    def test_v2_failure_after_saved_write_rolls_back_both_databases(self):
        config.save_feeds({'keep':['old']})
        config.set_playback_preferences({'sound':True})
        document = payload({'new':['creator']}, ['blocked'],version=2,
            saved_reels=[self.saved()], settings={'sound':False})
        original = backup._merge_settings
        def fail_after_settings(db, settings):
            original(db, settings)
            raise RuntimeError('storage error after both database writes')
        with patch.object(backup, '_merge_settings', side_effect=fail_after_settings):
            self.assertEqual(self.request(document)[0], 500)
        self.assertEqual(config.load_feeds(), {'keep':['old']})
        self.assertEqual(config.blocked_accounts(), set())
        self.assertEqual(recommendations.saved_reels(), [])
        self.assertTrue(config.playback_preferences()['sound'])
