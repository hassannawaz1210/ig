import shutil
import subprocess
import unittest
from pathlib import Path

from ig import views


class MediaZoomTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is needed for gesture checks')
    def test_pinch_and_desktop_zoom_preserve_media_and_isolate_gestures(self):
        subprocess.run(['node', str(Path(__file__).with_name('media_zoom.cjs')),
                        str(views.STATIC_DIR / 'zoom.js')], check=True)

    def test_reels_load_shared_zoom(self):
        page = views.reels_html()
        self.assertIn('/static/zoom.js', page)
        self.assertIn('/static/zoom.css', page)
        self.assertLess(page.index('/static/zoom.js'), page.index('/static/reels.js'))
