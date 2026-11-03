"""Exercise Settings against the real local API with an isolated SQLite profile."""
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from ig import config
from ig.server import Handler
from test_reel_navigation import find_chromium


class SettingsNavigationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is needed to drive the browser')
    def test_manage_feeds_and_playback(self):
        chromium = find_chromium()
        if not chromium:
            self.skipTest('no headless Chromium found')
        with tempfile.TemporaryDirectory() as directory, patch('ig.config.CONFIG_DIR', Path(directory)):
            config.save_feeds({'edits':['alpha','shared'], 'memes':['shared'],
                               'large':[f'creator{n:03d}' for n in range(160)]})
            config.set_blocked('blocked', True)
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                result = subprocess.run(['node', str(Path(__file__).with_name('settings_navigation.cjs')),
                    chromium, f'http://127.0.0.1:{server.server_address[1]}/settings'],
                    capture_output=True, text=True, timeout=60)
                if result.returncode == 77:
                    self.skipTest('headless Chromium could not start: ' + result.stderr[-300:])
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('shared', config.load_feeds()['memes'])
                self.assertNotIn('shared', config.load_feeds()['edits'])
                self.assertEqual(config.blocked_accounts(), {'blocked'})
            finally:
                server.shutdown()
                server.server_close()
