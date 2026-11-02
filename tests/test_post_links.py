import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ig import cli, config
from ig.links import post_code
from ig.server import Handler


class PostLinkTests(unittest.TestCase):
    def test_links_strip_tracking_query_and_fragment(self):
        for value in ['DfNNjp-xLDt', 'https://www.instagram.com/reel/DfNNjp-xLDt/?cplk=abc',
                      'https://instagram.com/p/DfNNjp-xLDt/#fragment']:
            self.assertEqual(post_code(value), 'DfNNjp-xLDt')

    def test_non_instagram_links_and_invalid_codes_rejected(self):
        for value in ['https://evil.test/reel/abcde', 'https://instagram.com.evil.test/p/abcde',
                      'https://user@instagram.com/p/abcde', 'https://instagram.com/creator/', 'abc', '../secret']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                post_code(value)

    def test_cli_opens_player_without_fetching_instagram(self):
        link = 'https://www.instagram.com/reel/DfNNjp-xLDt/?cplk=abc'
        for args in [['watch', link], [link], ['open', link], ['watch', 'DfNNjp-xLDt']]:
            with patch('ig.cli.open_user') as opened:
                cli.main(args)
                opened.assert_called_once_with('/reel/DfNNjp-xLDt')

    def test_direct_page_is_a_shell_and_does_not_require_feeds(self):
        with tempfile.TemporaryDirectory() as root, patch('ig.config.CONFIG_DIR', Path(root)):
            config.save_feeds({})
            for path in ['/reel/DfNNjp-xLDt', '/p/DfNNjp-xLDt/?x=1']:
                handler = Handler.__new__(Handler)
                handler.path = path
                with patch.object(handler, '_send') as send, patch('ig.server.media_info') as media:
                    handler.do_GET()
                media.assert_not_called()
                status, body, _ = send.call_args.args
                self.assertEqual(status, 200)
                boot = json.loads(body.decode().split('<script id="boot" type="application/json">')[1].split('</script>')[0])
                self.assertEqual(boot['post_code'], 'DfNNjp-xLDt')
                self.assertEqual(boot['feeds'], [])
