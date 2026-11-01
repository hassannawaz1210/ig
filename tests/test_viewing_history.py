import io
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from ig import config, feeds, history, recommendations as rec
from ig.server import Handler


class ViewingHistoryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        for target, value in [('ig.config.CONFIG_DIR', self.path),
                              ('ig.history.HISTORY_JSON', self.path / 'history.json')]:
            mock = patch(target, value)
            mock.start()
            self.addCleanup(mock.stop)
        config.save_feeds({'custom': ['creator']})

    def post(self, code='clip001'):
        return {'shortcode':code, 'owner':'creator', 'is_video':True}

    def request(self, path, body):
        handler = Handler.__new__(Handler)
        handler.path = path
        encoded = json.dumps(body).encode()
        handler.headers = {'Content-Length': str(len(encoded))}
        handler.rfile = io.BytesIO(encoded)
        with patch.object(handler, '_send') as send:
            handler.do_POST()
        return send.call_args.args[0]

    def test_reserving_does_not_mark_viewed_or_start_cooldown(self):
        posts = history.reserve([self.post()], 'client-one')
        rec.record_deliveries(posts)
        self.assertEqual(history.load_history()['codes'], [])
        self.assertEqual(rec.recent_creators(), [])
        self.assertEqual(rec.trial_cooldowns(set()), set())
        self.assertEqual(len(history.active_reservations()), 1)

    def test_parallel_reservations_cannot_claim_same_clip(self):
        with ThreadPoolExecutor(max_workers=2) as workers:
            batches = list(workers.map(lambda client: history.reserve([self.post()], client), ['client-one','client-two']))
        self.assertEqual(sum(map(len, batches)), 1)

    def test_release_is_scoped_to_client_and_tokens(self):
        post = history.reserve([self.post()], 'client-one')[0]
        history.release('client-two', [post['reservation']])
        self.assertEqual(len(history.active_reservations()), 1)
        history.release('client-one', [post['reservation']])
        self.assertEqual(history.active_reservations(), [])
        self.assertEqual(len(history.reserve([self.post()], 'client-two')), 1)

    def test_expiry_revives_an_exhausted_pool(self):
        state = {'posts':[self.post()], 'has_more':False}
        pool = feeds.make_pool(['creator'])
        with patch('ig.feeds.STATE', {'creator':state}), patch('ig.history.time.time', return_value=1000):
            first = feeds.pool_more(pool, client='client-one')['posts']
            self.assertEqual(len(first), 1)
            waiting = feeds.pool_more(pool, client='client-two')
            self.assertEqual(waiting['posts'], [])
            self.assertTrue(waiting['has_more'])
        with patch('ig.feeds.STATE', {'creator':state}), patch('ig.history.time.time', return_value=1000 + history.RESERVATION_TTL + 1):
            second = feeds.pool_more(pool, client='client-two')['posts']
        self.assertEqual(second[0]['shortcode'], first[0]['shortcode'])
        self.assertNotEqual(second[0]['reservation'], first[0]['reservation'])

    def test_renew_only_extends_live_owned_reservations(self):
        with patch('ig.history.time.time', return_value=1000):
            token = history.reserve([self.post()], 'client-one')[0]['reservation']
        with patch('ig.history.time.time', return_value=1100):
            history.renew('client-two', [token])
            history.renew('client-one', [token])
        with patch('ig.history.time.time', return_value=1150):
            self.assertEqual(len(history.active_reservations()), 1)
        with patch('ig.history.time.time', return_value=1300):
            history.renew('client-one', [token])
            self.assertEqual(history.active_reservations(), [])

    def test_playback_endpoint_is_idempotent_and_progress_never_decreases(self):
        token = history.reserve([self.post()], 'client-one')[0]['reservation']
        body = {'client':'client-one','code':'clip001','reservation':token,'seconds':1}
        self.assertEqual(self.request('/reels/view', body), 200)
        for seconds in [6, 6, 2]:
            self.assertEqual(self.request('/reels/view', dict(body, seconds=seconds)), 200)
        self.assertEqual(history.load_history()['codes'], ['clip001'])
        self.assertEqual(history.active_reservations(), [])
        self.assertEqual(rec.recent_creators(), ['creator'])
        self.assertEqual(rec.trial_cooldowns(set()), {'creator'})
        with history.database() as db:
            self.assertEqual(db.execute('SELECT seconds FROM viewings').fetchall(), [(6.0,)])
            self.assertEqual(db.execute('SELECT kind FROM reels').fetchone()[0], 'viewed')
        self.assertEqual(history.reserve([self.post()], 'client-two'), [])

    def test_foreign_token_and_invalid_progress_are_rejected(self):
        token = history.reserve([self.post()], 'client-one')[0]['reservation']
        body = {'client':'client-two','code':'clip001','reservation':token,'seconds':1}
        self.assertEqual(self.request('/reels/view', body), 409)
        for seconds in [0, -1, True, float('nan'), float('inf'), 3601]:
            self.assertEqual(self.request('/reels/view', dict(body, client='client-one', seconds=seconds)), 400)
        self.assertEqual(history.load_history()['codes'], [])

    def test_existing_history_is_preserved_as_legacy_not_reclassified(self):
        (self.path / 'history.json').write_text(json.dumps({'codes':['old001'],'last_owner':'oldcreator'}))
        db = sqlite3.connect(self.path / 'history.sqlite3')
        try:
            db.execute('BEGIN IMMEDIATE')
            history._initial_schema(db)
            db.execute('PRAGMA user_version=1')
            db.commit()
        finally:
            db.close()
        self.assertEqual(history.load_history(), {'codes':['old001'],'last_owner':'oldcreator'})
        with history.database() as db:
            self.assertEqual(db.execute('SELECT code,kind FROM reels').fetchall(), [('old001','legacy_delivery')])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM viewings').fetchone()[0], 0)
        self.assertEqual(history.reserve([self.post('old001')], 'client-one'), [])


    def test_delayed_playback_can_be_recorded_after_release(self):
        token = history.reserve([self.post()], 'client-one')[0]['reservation']
        history.release('client-one', [token])
        self.assertEqual(history.record_playback('clip001', token, 'client-one', 1.0), 'creator')
        self.assertEqual(history.load_history()['codes'], ['clip001'])

    def test_delivery_receipt_cannot_record_another_clients_playback(self):
        token = history.reserve([self.post()], 'client-one')[0]['reservation']
        history.release('client-one', [token])
        self.assertIsNone(history.record_playback('clip001', token, 'client-two', 1.0))
        self.assertEqual(history.load_history()['codes'], [])
