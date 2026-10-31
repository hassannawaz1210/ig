import io
import json
from unittest.mock import patch
from test_ig import ProjectTests
from ig import config, feeds
from ig.server import Handler


class PreferenceTests(ProjectTests):
    def test_toggle_persists_both_states(self):
        self.assertFalse(config.prefer_memes())
        for enabled in (True, False):
            handler = Handler.__new__(Handler)
            handler.path = '/preferences'
            body = json.dumps({'prefer_memes': enabled}).encode()
            handler.headers = {'Content-Length': str(len(body))}
            handler.rfile = io.BytesIO(body)
            with patch.object(handler, '_send') as send:
                handler.do_POST()
                self.assertEqual(send.call_args.args[0], 200)
            self.assertEqual(config.prefer_memes(), enabled)

    def test_selection_groups_and_off_switch(self):
        config.save_feeds({'memes': ['meme'], 'other': ['other']})
        pool = feeds.make_pool(['meme', 'other'])
        feeds.discover_into(pool, ['newmeme'], meme_source=True)
        candidates = feeds.available_accounts(pool, set())
        with patch('ig.feeds.random.choices', return_value=[['newmeme']]) as choose:
            self.assertEqual(feeds.choose_account(pool, candidates, True), 'newmeme')
            self.assertEqual(choose.call_args.kwargs['weights'], [70, 20, 10])
        with patch('ig.feeds.random.choices') as choose:
            self.assertIn(feeds.choose_account(pool, candidates, False), candidates)
            choose.assert_not_called()
        self.assertEqual(feeds.choose_account(pool, ['other'], True), 'other')

    def test_only_meme_mentions_get_meme_discovery(self):
        config.save_feeds({'memes': ['meme']})
        pool = feeds.make_pool(['meme'])
        handler = Handler.__new__(Handler)
        with patch('ig.server.media_info', return_value={
            'owner': 'meme', 'tagged_users': ['newmeme'], 'commenters': ['viewer']}):
            handler._media('code', pool)
        self.assertEqual(pool['meme_discoveries'], set())
        self.assertNotIn('newmeme', pool['discovered'])
        self.assertNotIn('viewer', pool['discovered'])

    def test_combination_selection_persists_and_rejects_invalid_feeds(self):
        config.save_feeds({'memes': ['brainrot'], 'electronics': ['adafruit'], 'empty': []})
        for choice in [['memes'], ['memes', 'electronics'], ['empty'], None]:
            config.set_selected_feeds(choice)
            self.assertEqual(config.selected_feeds(), None if choice is None else sorted(choice))
        for invalid in [[], ['unknown'], 'memes', [1]]:
            with self.assertRaises(ValueError):
                config.set_selected_feeds(invalid)
        self.assertIsNone(config.selected_feeds())

    def test_selection_endpoint_get_and_post(self):
        config.save_feeds({'memes': [], 'electronics': []})
        handler = Handler.__new__(Handler)
        handler.path = '/preferences'
        body = json.dumps({'selected_feeds': ['electronics', 'memes']}).encode()
        handler.headers = {'Content-Length': str(len(body))}
        handler.rfile = io.BytesIO(body)
        with patch.object(handler, '_send') as send:
            handler.do_POST()
            self.assertEqual(send.call_args.args[0], 200)
        with patch.object(handler, '_send') as send:
            handler.do_GET()
            self.assertEqual(json.loads(send.call_args.args[1])['selected_feeds'], ['electronics', 'memes'])

    def test_deleted_selected_feed_does_not_fall_back_to_all(self):
        config.save_feeds({'memes': [], 'electronics': []})
        config.set_selected_feeds(['memes'])
        config.save_feeds({'electronics': []})
        self.assertEqual(config.selected_feeds(), [])
