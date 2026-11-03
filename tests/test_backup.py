import io
import json
from unittest.mock import patch

from ig import backup, config
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
            if path == '/feeds/export':
                handler.do_GET()
            else:
                handler.do_POST()
        return send.call_args.args if send.called else None

    def test_export_round_trip_only_includes_feeds_and_permanent_blocks(self):
        config.save_feeds({'edits': ['one', 'two'], 'empty': []})
        config.set_blocked('blocked', True)
        config.set_selected_feeds(['edits'])
        config.set_prefer_memes(True)
        response = self.request(path='/feeds/export')
        self.assertEqual(response[0], 200)
        self.assertIn('attachment; filename="ig-feeds-', response[3]['Content-Disposition'])
        document = json.loads(response[1])
        self.assertEqual(document, payload({'edits': ['one', 'two'], 'empty': []}, ['blocked']))
        self.assertEqual(backup.parse(response[1]), document)

    def test_preview_does_not_write_and_counts_memberships(self):
        config.save_feeds({'existing': ['keep']})
        response = self.request(payload({'one': ['same'], 'two': ['same', 'other']}, ['bad']),
                                '/feeds/import/preview')
        self.assertEqual(response[0], 200)
        self.assertEqual(json.loads(response[1]), {'feeds': 2, 'accounts': 3, 'blocked': 1, 'names': ['one', 'two']})
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
        documents = [payload(version=2), payload(version=True), payload(format='other'),
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
