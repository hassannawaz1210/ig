"""The stats page: permanent watch log, numbers, and the page itself."""
import io
import json
import shutil
import subprocess
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from ig import config, history, recommendations as rec, stats
from ig.server import Handler
from test_ig import ProjectTests

NOW = time.mktime((2026, 11, 9, 12, 0, 0, 0, 0, -1))
DAY = 86400


class StatsTests(ProjectTests):
    def setUp(self):
        super().setUp()
        config.save_feeds({'memes': ['saved_one'], 'edits': ['editor']})
        rec.remember_reels([
            {'shortcode': 'MEME0000001', 'owner': 'saved_one', 'caption': 'lol #shitpost'},
            {'shortcode': 'EDIT0000001', 'owner': 'editor', 'caption': 'film #movieedit'},
            {'shortcode': 'NEW00000001', 'owner': 'fresh', 'caption': 'new #shitpost', 'discovery_source': 'suggested'},
            {'shortcode': 'OLD00000001', 'owner': 'editor', 'caption': 'old #oldtag'},
        ])
        self.log('t1', 'MEME0000001', 'saved_one', 30, NOW - 1 * DAY)
        self.log('t2', 'EDIT0000001', 'editor', 10, NOW - 2 * DAY)
        self.log('t3', 'NEW00000001', 'fresh', 45, NOW - 3 * DAY)
        self.log('t4', 'OLD00000001', 'editor', 120, NOW - 40 * DAY)

    def react(self):
        rec.set_feedback('NEW00000001', 'like')
        rec.set_feedback('EDIT0000001', 'skip')

    def log(self, token, code, owner, seconds, when):
        rec.log_watch(token, code, owner, seconds, viewed=when)

    def test_watch_log_keeps_source_feed_and_the_longest_watch(self):
        rec.log_watch('t1', 'MEME0000001', 'saved_one', 12, viewed=NOW)   # an earlier, shorter report
        rec.log_watch('t1', 'MEME0000001', 'saved_one', 50, viewed=NOW)
        with rec.database() as connection:
            rows = dict((token, (seconds, source, feed)) for token, seconds, source, feed in
                        connection.execute('SELECT token, seconds, source, feed FROM watch_log'))
        self.assertEqual(rows['t1'], (50, 'feed', 'memes'))
        self.assertEqual(rows['t3'], (45, 'suggested', None))

    def test_summary_creators_and_sources_for_a_period(self):
        self.react()
        data = stats.compute(7, now=NOW)
        self.assertEqual(data['summary']['reels'], 3, 'the 40-day-old view is outside 7 days')
        self.assertEqual(data['summary']['seconds'], 85)
        self.assertEqual(data['summary']['new_creators'], 1)
        self.assertEqual([c['owner'] for c in data['creators']], ['fresh', 'saved_one', 'editor'])
        sources = {s['source']: s for s in data['sources']}
        self.assertEqual(sources['suggested']['like_rate'], 1.0)
        self.assertEqual(sources['feed']['reels'], 2)
        self.assertEqual(sources['feed']['skip_rate'], 0.5)
        self.assertEqual([s['source'] for s in data['sources']], ['feed', 'suggested'], 'fixed source order')
        self.assertEqual(stats.compute(0, now=NOW)['summary']['reels'], 4)

    def test_weeks_split_by_feed_and_hours(self):
        data = stats.compute(0, now=NOW)
        keys = [s['key'] for s in data['weeks']['series']]
        self.assertEqual(keys[-1], '__new__', 'new creators come last')
        self.assertIn('edits', keys)
        totals = sum(sum(w['seconds'].values()) for w in data['weeks']['weeks'])
        self.assertEqual(totals, 205)
        self.assertGreaterEqual(len(data['weeks']['weeks']), 4)
        self.assertEqual(sum(data['hours']), 205)

    def test_rising_and_fading_hashtags(self):
        tags = stats.compute(0, now=NOW)['tags']
        self.assertTrue(tags['enough_history'])
        self.assertIn('shitpost', [t['tag'] for t in tags['rising']])
        self.assertIn('oldtag', [t['tag'] for t in tags['falling']])

    def test_backfill_copies_views_the_log_lacks_once(self):
        with history.database() as db:
            db.execute('INSERT INTO viewings VALUES (?,?,?,?,?,?)', ('h1', 'MEME0000001', 'c', 'saved_one', NOW - DAY, 9))
            db.execute('INSERT INTO viewings VALUES (?,?,?,?,?,?)', ('t1', 'MEME0000001', 'c', 'saved_one', NOW - DAY, 9))
        with patch.object(stats, '_backfilled', False):
            self.assertEqual(stats.backfill(), 1, 'only the view the log lacks')
            self.assertEqual(stats.backfill(), 0, 'once per start')


class StatsRouteTests(ProjectTests):
    def get(self, path):
        handler = Handler.__new__(Handler)
        handler.path = path
        handler.headers = {}
        handler.rfile = io.BytesIO()
        with patch.object(handler, '_send') as send, patch.object(handler, '_allowed', return_value=True):
            handler.do_GET()
        return send.call_args.args

    def test_page_data_and_bad_period(self):
        status, body, kind = self.get('/stats')[:3]
        self.assertEqual((status, kind), (200, 'text/html; charset=utf-8'))
        self.assertIn(b'Your stats', body)
        status, body = self.get('/stats/data?days=7')[:2]
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['days'], 7)
        self.assertEqual(self.get('/stats/data?days=5')[0], 400)
        # Each section can load on its own, so the page shows whichever is ready first.
        self.assertEqual(set(json.loads(self.get('/stats/data?days=7&part=overview')[1])),
                         {'days', 'since', 'summary', 'creators', 'sources', 'hours'})
        self.assertEqual(set(json.loads(self.get('/stats/data?days=7&part=weeks')[1])), {'weeks'})
        self.assertEqual(set(json.loads(self.get('/stats/data?days=7&part=tags')[1])), {'tags'})
        self.assertEqual(self.get('/stats/data?days=7&part=secrets')[0], 400)

    def test_failures_return_a_safe_error(self):
        with patch.object(stats, 'compute', side_effect=RuntimeError('database is locked /home/x')):
            status, body = self.get('/stats/data?days=30')[:2]
        self.assertGreaterEqual(status, 500)
        self.assertNotIn(b'/home/x', body)


class StatsPageTests(StatsTests):
    """The page itself, in a real browser."""
    def test_page_in_a_browser(self):
        from test_reel_navigation import find_chromium
        chromium = find_chromium()
        if not shutil.which('node') or not chromium:
            self.skipTest('needs Node.js and headless Chromium')
        # The fixture's views are relative to NOW; move them to the real clock.
        shift = time.time() - NOW
        with rec.database() as connection:
            connection.execute('UPDATE watch_log SET viewed = viewed + ?', (shift,))
        with patch.object(stats, '_backfilled', True), patch.object(Handler, '_allowed', return_value=True):
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                result = subprocess.run(['node', str(Path(__file__).with_name('stats_page.cjs')), chromium,
                                         f'http://127.0.0.1:{server.server_address[1]}/stats'],
                                        capture_output=True, text=True, timeout=90)
            finally:
                server.shutdown()
        if result.returncode == 77:
            self.skipTest('headless Chromium could not start')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('passed', result.stdout)
