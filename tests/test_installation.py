"""Fresh user profiles and platform browser launch, without Instagram requests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ig.server import open_in_browser


class InstallationTests(unittest.TestCase):
    def test_separate_users_start_empty_and_keep_independent_feeds(self):
        with tempfile.TemporaryDirectory() as root:
            def run(user, code):
                env = dict(os.environ, XDG_CONFIG_HOME=str(Path(root) / user / 'config'),
                           XDG_CACHE_HOME=str(Path(root) / user / 'cache'))
                return subprocess.check_output([sys.executable, '-c', code], env=env, text=True)

            inspect = ('import json; from ig.config import load_feeds, blocked_accounts; '
                       'print(json.dumps([load_feeds(), sorted(blocked_accounts())]))')
            for user in ('alice', 'bob'):
                self.assertEqual(json.loads(run(user, inspect)), [{}, []])
            run('alice', 'from ig.cli import main; main(["add", "space", "nasa"])')
            run('bob', 'from ig.cli import main; main(["add", "science", "esa"])')
            self.assertEqual(json.loads(run('alice', inspect)), [{'space': ['nasa']}, []])
            self.assertEqual(json.loads(run('bob', inspect)), [{'science': ['esa']}, []])
            page = run('bob', 'from ig.views import reels_html; print(reels_html())')
            self.assertIn('</html>', page)
            self.assertIn('science', page)

    def test_native_browser_on_other_platforms(self):
        for platform in ('darwin', 'win32'):
            with self.subTest(platform=platform), patch('sys.platform', platform), \
                    patch('webbrowser.open', return_value=True) as browser, \
                    patch('ig.server.subprocess.run') as command:
                self.assertTrue(open_in_browser('http://127.0.0.1:7349'))
                browser.assert_called_once_with('http://127.0.0.1:7349')
                command.assert_not_called()

    def test_browser_environment_preference(self):
        with patch.dict(os.environ, {'BROWSER': 'my-browser'}), \
                patch('webbrowser.open', return_value=True) as browser:
            self.assertTrue(open_in_browser('http://127.0.0.1:7349'))
            browser.assert_called_once()

    def test_missing_native_browser_leaves_url_for_manual_open(self):
        with patch('sys.platform', 'darwin'), patch('webbrowser.open', return_value=False):
            self.assertFalse(open_in_browser('http://127.0.0.1:7349'))
