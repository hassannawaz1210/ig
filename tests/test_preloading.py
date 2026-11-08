import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from ig import views


class PreloadingTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is needed for browser logic checks')
    def test_preloading_and_pagination(self):
        # The browser loads these three scripts in this order (see reels.html).
        script = '\n'.join(views.static_file(name)[0].decode()
                           for name in ('icons.js', 'common.js', 'reels.js'))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'reels.js'
            path.write_text(script)
            subprocess.run(['node', str(Path(__file__).with_name('preloading.cjs')), str(path)], check=True)


class PageSyncTests(unittest.TestCase):
    def test_changes_reach_other_pages_and_frozen_pages_catch_up(self):
        script = Path(__file__).with_name('page_sync.cjs')
        common = Path(__file__).resolve().parents[1] / 'src/ig/static/common.js'
        subprocess.run(['node', str(script), str(common)], check=True)


class CommentRowTests(unittest.TestCase):
    def test_comment_rows_look_like_instagram_and_escape_text(self):
        root = Path(__file__).resolve().parents[1]
        subprocess.run(['node', str(Path(__file__).with_name('comments_render.cjs')),
                        str(root / 'src/ig/static/comments.js')], check=True)
