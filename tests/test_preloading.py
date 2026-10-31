import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ig.views import reels_html


class PreloadingTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is needed for browser logic checks')
    def test_preloading_and_pagination(self):
        with patch('ig.views.feed_accounts', return_value=set()), \
             patch('ig.views.blocked_accounts', return_value=set()), \
             patch('ig.views.selected_feeds', return_value=None), \
             patch('ig.views.load_feeds', return_value={}):
            script = re.findall(r'<script>(.*?)</script>', reels_html([]), re.S)[0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'reels.js'
            path.write_text(script)
            subprocess.run(['node', str(Path(__file__).with_name('preloading.cjs')), str(path)], check=True)
