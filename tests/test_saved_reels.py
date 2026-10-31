import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ig import cli, config, feeds, recommendations as rec
from ig.migrations import migrate
from ig.server import Handler


class SavedReelsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher = patch('ig.config.CONFIG_DIR', Path(self.directory.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        config.save_feeds({'memes': ['creator']})

    def post(self, code='clip01', owner='creator', **extra):
        return {'shortcode': code, 'owner': owner, 'caption': 'A reel #gym', 'is_video': True, **extra}

    def request(self, method, path, body=None, raw=None):
        handler = Handler.__new__(Handler)
        handler.path = path
        data = raw if raw is not None else json.dumps(body).encode() if body is not None else b''
        handler.headers = {'Content-Length': str(len(data))}
        handler.rfile = io.BytesIO(data)
        with patch.object(handler, '_send') as send:
            getattr(handler, 'do_' + method)()
        return send.call_args.args

    # Storage

    def test_save_keeps_metadata_and_unsave_removes_it(self):
        rec.remember_reels([self.post()])
        details = {'url': 'https://www.instagram.com/p/clip01/', 'taken_at': 1700000000,
                   'like_count': 12, 'comment_count': 3, 'image': 'https://scontent.cdninstagram.com/a.jpg'}
        self.assertTrue(rec.set_reel_saved('clip01', True, details))
        [saved] = rec.saved_reels()
        self.assertEqual((saved['shortcode'], saved['owner'], saved['caption'], saved['like_count'],
                          saved['comment_count'], saved['taken_at'], saved['image']),
                         ('clip01', 'creator', 'A reel #gym', 12, 3, 1700000000,
                          'https://scontent.cdninstagram.com/a.jpg'))
        self.assertEqual(rec.saved_codes(['clip01', 'other']), {'clip01'})
        self.assertFalse(rec.set_reel_saved('clip01', False))
        self.assertEqual(rec.saved_reels(), [])

    def test_saving_is_not_a_like_and_does_not_change_feeds(self):
        rec.remember_reels([self.post()])
        rec.set_reel_saved('clip01', True)
        self.assertEqual(rec.feedback_for(['clip01']), {})
        self.assertEqual(rec.liked_reels(), [])
        self.assertEqual(config.load_feeds(), {'memes': ['creator']})

    def test_newest_first_and_resave_keeps_saved_time(self):
        rec.remember_reels([self.post('clip01'), self.post('clip02')])
        with patch('ig.recommendations.time.time', return_value=100):
            rec.set_reel_saved('clip01', True)
        with patch('ig.recommendations.time.time', return_value=200):
            rec.set_reel_saved('clip02', True)
        with patch('ig.recommendations.time.time', return_value=300):
            rec.set_reel_saved('clip01', True, {'like_count': 5})
        reels = rec.saved_reels()
        self.assertEqual([r['shortcode'] for r in reels], ['clip02', 'clip01'])
        self.assertEqual(reels[1]['like_count'], 5)

    def test_unknown_reel_and_bad_metadata(self):
        with self.assertRaises(KeyError):
            rec.set_reel_saved('missing1', True)
        with self.assertRaises(ValueError):
            rec.set_reel_saved('missing1', 'yes')
        rec.remember_reels([self.post()])
        rec.set_reel_saved('clip01', True, {'image': 'javascript:alert(1)', 'url': 'http://evil.test',
                                            'like_count': True, 'taken_at': 'soon'})
        [saved] = rec.saved_reels()
        self.assertIsNone(saved['image'])
        self.assertEqual(saved['url'], 'https://www.instagram.com/p/clip01/')
        self.assertIsNone(saved['like_count'])
        self.assertIsNone(saved['taken_at'])

    def test_pruning_keeps_saved_reels(self):
        with patch('ig.recommendations.MAX_REELS', 2), patch('ig.recommendations.PRUNE_SLACK', 0):
            with patch('ig.recommendations.time.time', return_value=1):
                rec.remember_reels([self.post('old001'), self.post('old002')])
            rec.set_reel_saved('old001', True)
            for i in range(3, 7):
                with patch('ig.recommendations.time.time', return_value=i):
                    rec.remember_reels([self.post(f'new00{i}')])
            self.assertIsNone(rec.stored_reel('old002'))
            self.assertIsNotNone(rec.stored_reel('old001'))
        self.assertEqual([r['shortcode'] for r in rec.saved_reels()], ['old001'])

    def test_migration_upgrades_an_existing_version_2_database(self):
        path = Path(self.directory.name) / 'recommendations.sqlite3'
        connection = sqlite3.connect(path)
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            migrate(connection, (rec._initial_schema, rec._tag_index_schema))
            connection.execute('INSERT INTO reels VALUES (?, ?, ?)',
                               ('clip01', json.dumps(self.post()), 1.0))
            connection.execute("INSERT INTO feedback VALUES ('clip01', 'like', 1.0)")
        self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 2)
        connection.close()
        self.assertTrue(rec.set_reel_saved('clip01', True))
        connection = sqlite3.connect(path)
        self.addCleanup(connection.close)
        self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 3)
        self.assertEqual(rec.feedback_for(['clip01']), {'clip01': 'like'})
        self.assertEqual(len(rec.saved_reels()), 1)

    # Delivered posts

    def test_delivered_posts_carry_saved_flag(self):
        state = {'posts': [self.post(f'clip0{i}') for i in range(1, 3)], 'has_more': False}
        rec.remember_reels(state['posts'])
        rec.set_reel_saved('clip01', True)
        with patch('ig.feeds.STATE', {'creator': state}), \
                patch('ig.history.HISTORY_JSON', Path(self.directory.name) / 'history.json'), \
                patch('ig.feeds.load_feeds', lambda: {'memes': ['creator']}), \
                patch('ig.feeds.prefer_memes', lambda: False):
            posts = feeds.pool_more(feeds.make_pool(['creator']))['posts']
        self.assertTrue(posts)
        for post in posts:
            self.assertEqual(post['saved'], post['shortcode'] == 'clip01')

    # Endpoint

    def test_endpoint_saves_with_cached_profile_metadata(self):
        rec.remember_reels([self.post()])
        state = {'posts': [self.post(like_count=7, image='https://scontent.cdninstagram.com/p.jpg',
                                     url='https://www.instagram.com/p/clip01/', taken_at=1700000000)]}
        with patch('ig.server.STATE', {'creator': state}):
            self.assertEqual(self.request('POST', '/saved-reels', {'code': 'clip01', 'saved': True})[0], 200)
        [saved] = rec.saved_reels()
        self.assertEqual((saved['like_count'], saved['image']), (7, 'https://scontent.cdninstagram.com/p.jpg'))
        self.assertEqual(self.request('POST', '/saved-reels', {'code': 'clip01', 'saved': False})[:2],
                         (200, b'{"saved": false}'))
        self.assertEqual(rec.saved_reels(), [])

    def test_endpoint_saves_when_profile_cache_fails(self):
        rec.remember_reels([self.post()])
        with patch('ig.server.STATE', {}), patch('ig.server.PROFILES.cached', side_effect=sqlite3.Error('locked')), \
                self.assertLogs('ig.server', 'WARNING'):
            self.assertEqual(self.request('POST', '/saved-reels', {'code': 'clip01', 'saved': True})[0], 200)
        self.assertEqual(len(rec.saved_reels()), 1)

    def test_endpoint_validation(self):
        rec.remember_reels([self.post()])
        bad = [{'code': 'clip01', 'saved': 'yes'}, {'code': 'clip01', 'saved': 1}, {'code': 'clip01'},
               {'code': '../x', 'saved': True}, {'code': 7, 'saved': True}, ['clip01']]
        for body in bad:
            with self.subTest(body=body):
                self.assertEqual(self.request('POST', '/saved-reels', body)[0], 400)
        self.assertEqual(self.request('POST', '/saved-reels', raw=b'not json')[0], 400)
        self.assertEqual(self.request('POST', '/saved-reels', raw=b'')[0], 400)
        self.assertEqual(self.request('POST', '/saved-reels', raw=b' ' * 2000)[0], 400)
        self.assertEqual(self.request('POST', '/saved-reels', {'code': 'unknown1', 'saved': True})[0], 404)
        self.assertEqual(rec.saved_reels(), [])

    def test_endpoint_storage_failure_is_logged_with_safe_message(self):
        with patch('ig.server.recommendations.set_reel_saved', side_effect=sqlite3.OperationalError('disk')), \
                patch('ig.server.saved_reel_details', return_value={}), self.assertLogs('ig.server', 'ERROR'):
            status, body, _ = self.request('POST', '/saved-reels', {'code': 'clip01', 'saved': True})
        self.assertEqual((status, body), (500, b'could not update Saved reels'))

    def test_saved_reels_page_lists_reels_and_routes_media(self):
        rec.remember_reels([self.post(caption='</script><b>x</b>')])
        rec.set_reel_saved('clip01', True)
        status, body, ctype = self.request('GET', '/saved-reels')
        html = body.decode()
        self.assertEqual(status, 200)
        self.assertIn('Saved reels', html)
        self.assertIn('"clip01"', html)
        self.assertNotIn('</script><b>', html)
        self.assertNotIn('id="more"', html)
        with patch.object(Handler, '_media', return_value={'video': 'https://v.test/a.mp4'}) as media:
            status, body, _ = self.request('POST', '/saved-reels/media', {'code': 'clip01'})
        self.assertEqual(status, 200)
        media.assert_called_once_with('clip01')

    def test_saved_reels_page_failure_is_safe(self):
        with patch('ig.server.recommendations.saved_reels', side_effect=sqlite3.OperationalError('disk')), \
                self.assertLogs('ig.server', 'ERROR'):
            status, body, _ = self.request('GET', '/saved-reels')
        self.assertEqual(status, 500)
        self.assertNotIn(b'disk', body)

    # Command line

    def test_cli_lists_saved_reels(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(['saved-reels'])
        self.assertIn('No saved reels yet', out.getvalue())
        rec.remember_reels([self.post()])
        rec.set_reel_saved('clip01', True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), patch('ig.cli.open_user') as open_user:
            cli.main(['saved-reels'])
        open_user.assert_not_called()
        line = out.getvalue().strip()
        self.assertIn('clip01', line)
        self.assertIn('@creator', line)
        self.assertIn('https://www.instagram.com/p/clip01/', line)


if __name__ == '__main__':
    unittest.main()
