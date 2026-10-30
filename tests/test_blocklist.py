import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ig import config, feeds
from ig.server import Handler


class BlocklistTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        for target, value in [('ig.config.CONFIG_DIR', Path(self.directory.name)),
                              ('ig.feeds.load_feeds', lambda: {'memes': ['saved', 'other']})]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_permanent_block_persists_and_excludes_saved_and_discovered(self):
        config.set_blocked('@SAVED', True)
        config.set_blocked('discovered', True)
        pool = feeds.make_pool(['saved', 'other', 'discovered'])
        self.assertEqual(feeds.available_accounts(pool, set()), ['other'])
        self.assertEqual(json.loads((config.CONFIG_DIR / 'blocked.json').read_text()),
                         ['discovered', 'saved'])
        config.set_blocked('saved', False)
        self.assertIn('saved', feeds.available_accounts(pool, set()))

    def test_discovery_cannot_reintroduce_blocked_account(self):
        config.set_blocked('unwanted', True)
        pool = feeds.make_pool([])
        feeds.discover_into(pool, ['unwanted', 'new'], meme_source=True)
        self.assertEqual(pool['discovered'], {'new'})
        self.assertEqual(pool['meme_discoveries'], {'new'})

    def test_session_exclusion_only_applies_to_its_request(self):
        states = {u: {'posts': [{'shortcode': u, 'owner': u, 'is_video': True}],
                      'has_more': False} for u in ['saved', 'other']}
        with patch('ig.feeds.STATE', states), patch('ig.feeds.prefer_memes', return_value=False):
            result = feeds._pool_more(feeds.make_pool(['saved', 'other']), excluded={'saved'})
            self.assertEqual([p['owner'] for p in result['posts']], ['other'])
            result = feeds._pool_more(feeds.make_pool(['saved', 'other']))
            self.assertEqual({p['owner'] for p in result['posts']}, {'other', 'saved'})
        self.assertEqual(config.blocked_accounts(), set())

    def test_block_and_unblock_routes(self):
        handler = Handler.__new__(Handler)
        for path, expected in [('/block', {'saved'}), ('/unblock', set())]:
            handler.path = path
            body = json.dumps({'username': '@Saved'}).encode()
            handler.headers = {'Content-Length': str(len(body))}
            handler.rfile = io.BytesIO(body)
            with patch.object(handler, '_send') as send:
                handler.do_POST()
                self.assertEqual(send.call_args.args[0], 200)
            self.assertEqual(config.blocked_accounts(), expected)

    def test_reject_invalid_session_list(self):
        handler = Handler.__new__(Handler)
        handler.path = '/reels/more'
        body = json.dumps({'session_blocked': 'not-a-list'}).encode()
        handler.headers = {'Content-Length': str(len(body))}
        handler.rfile = io.BytesIO(body)
        with patch('ig.server.feed_pool', return_value=feeds.make_pool([])), \
             patch.object(handler, '_send') as send:
            handler.do_POST()
        self.assertEqual(send.call_args.args[0], 400)
