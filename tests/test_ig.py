import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ig import cli, config, feeds, instagram, views


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = Path(self.tmp.name)
        # Watch history too: tests must never read or write the real ~/.cache/ig history.
        for target, value in [('ig.config.CONFIG_DIR', path), ('ig.history.HISTORY_JSON', path / 'history.json')]:
            p = patch(target, value)
            p.start()
            self.addCleanup(p.stop)
        # Never fetch Instagram topic pages in the background during tests.
        for service in (feeds.TOPICS, feeds.SUGGESTIONS):
            offline = patch.object(service, 'enabled', False)
            offline.start()
            self.addCleanup(offline.stop)
        # A rate-limit pause left by another test must not leak into this one.
        instagram.BACKOFF.reset()
        self.addCleanup(instagram.BACKOFF.reset)
        feeds.POOLS.clear()

    def run_cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            cli.main(args)
        return output.getvalue()

    def test_saved_feeds_ignore_legacy_weights(self):
        config.save_feeds({'one': {'accounts': ['nasa'], 'weight': 0},
                           'two': ['spacex', 'nasa']})
        self.assertEqual(feeds._pool_seeds(feeds.MIX), ['nasa', 'spacex'])
        pool = feeds.feed_pool(feeds.MIX)
        pool['discovered'].add('esa')
        self.assertEqual(feeds.available_accounts(pool, {'spacex'}), ['esa', 'nasa'])

    def test_feed_commands_round_trip(self):
        config.save_feeds({})
        self.run_cli('add', 'space', '@NASA', 'nasa', 'spacex')
        self.assertEqual(config.load_feeds(), {'space': ['nasa', 'spacex']})
        self.run_cli('rm', 'space', 'nasa')
        self.assertIn('spacex', self.run_cli('feeds'))
        self.run_cli('rm', 'space')
        self.assertEqual(config.load_feeds(), {})

    def test_cli_shortcuts_and_help(self):
        with patch('ig.cli.open_user') as opened:
            self.run_cli()
            opened.assert_called_with('/')
            for alias in ('reels', 'all', 'REELS'):
                self.run_cli(alias)
                opened.assert_called_with('/')
            self.run_cli('@NASA')
            opened.assert_called_with('/u/nasa')
        self.assertIn('ig serve', self.run_cli('--help'))
        with self.assertRaises(SystemExit):
            self.run_cli('profile')

    def test_views_render_without_weight_controls(self):
        config.save_feeds({})
        for html in [views.reels_html(), views.viewer_html('Profile', '', [], 'nasa')]:
            self.assertIn('</html>', html)
            self.assertNotIn('weightOp', html)
            self.assertNotIn('mixpanel', html)

    def test_reels_page_is_sent_before_any_batch(self):
        from ig.server import Handler
        config.save_feeds({'memes': ['meme']})
        for path in ('/', '/reels'):
            handler = Handler.__new__(Handler)
            handler.path = path
            with patch('ig.server.pool_more') as pool_more, patch('ig.server.feed_pool') as feed_pool, \
                    patch.object(handler, '_send') as send:
                handler.do_GET()
            pool_more.assert_not_called()
            feed_pool.assert_not_called()
            status, body, content_type = send.call_args.args
            self.assertEqual(status, 200)
            self.assertTrue(content_type.startswith('text/html'))
            html = body.decode()
            boot = json.loads(html.split('<script id="boot" type="application/json">')[1].split('</script>')[0])
            self.assertNotIn('posts', boot)
            self.assertIn('Loading reels…', html)
            self.assertIn('data-feed="memes"', html)
            self.assertNotIn('apply-feeds', html)

    def test_reels_page_failure_returns_safe_error(self):
        from ig.server import Handler
        handler = Handler.__new__(Handler)
        handler.path = '/'
        with patch('ig.server.reels_html', side_effect=RuntimeError('secret detail')), \
                patch.object(handler, '_send') as send, self.assertLogs('ig', level='ERROR'):
            handler.do_GET()
        status, body, _ = send.call_args.args
        self.assertEqual(status, 500)
        self.assertNotIn(b'secret detail', body)

    def test_instagram_payload_parser(self):
        data = {'xig_user_by_username': {'id': '1'},
                'polaris_ordered_timeline_connection': {'edges': [], 'page_info': {}}}
        html = '<script type="application/json" data-sjs>' + json.dumps(data) + '</script>'
        user, connection, token = instagram.parse_profile(html)
        self.assertEqual(user['id'], '1')
        self.assertEqual(connection['edges'], [])


    def test_age_restricted_profile_says_so_instead_of_blaming_rate_limits(self):
        restricted = '<title>Restricted profile</title><h2>Restricted profile</h2>'
        with self.assertRaises(instagram.ProfileUnavailable) as caught:
            instagram.parse_profile(restricted, 'adult_account')
        self.assertIn('@adult_account is age-restricted', str(caught.exception))
        # Without the age gate, an empty page is still treated as a possible rate limit.
        with self.assertRaises(instagram.UnexpectedResponse) as caught:
            instagram.parse_profile('<html></html>', 'someone')
        self.assertIn('rate limiting', str(caught.exception))
        # A profile with posts is unaffected, even if the words appear on the page.
        data = {'xig_user_by_username': {'id': '1'},
                'polaris_ordered_timeline_connection': {'edges': [{'node': {}}], 'page_info': {}}}
        html = ('<p>Restricted profile</p><script type="application/json" data-sjs>' + json.dumps(data) + '</script>')
        self.assertEqual(instagram.parse_profile(html, 'fine')[0]['id'], '1')

if __name__ == '__main__':
    unittest.main()
