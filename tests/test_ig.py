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
        for target, value in [('ig.config.CONFIG_DIR', path),
                              ('ig.config.FEEDS_JSON', path / 'feeds.json'),
                              ('ig.feeds.FEEDS_JSON', path / 'feeds.json')]:
            p = patch(target, value)
            p.start()
            self.addCleanup(p.stop)
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
            self.run_cli('@NASA')
            opened.assert_called_with('/u/nasa')
        self.assertIn('ig serve', self.run_cli('--help'))
        with self.assertRaises(SystemExit):
            self.run_cli('profile')

    def test_views_render_without_weight_controls(self):
        config.save_feeds({})
        for html in [views.reels_html([]), views.viewer_html('Profile', '', [], 'nasa')]:
            self.assertIn('</html>', html)
            self.assertNotIn('weightOp', html)
            self.assertNotIn('mixpanel', html)

    def test_instagram_payload_parser(self):
        data = {'xig_user_by_username': {'id': '1'},
                'polaris_ordered_timeline_connection': {'edges': [], 'page_info': {}}}
        html = '<script type="application/json" data-sjs>' + json.dumps(data) + '</script>'
        user, connection, token = instagram.parse_profile(html)
        self.assertEqual(user['id'], '1')
        self.assertEqual(connection['edges'], [])


if __name__ == '__main__':
    unittest.main()
