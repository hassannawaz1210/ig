"""Profile viewer in a real browser: multi-image posts swipe sideways like
Instagram, previews appear at once, and missing previews are filled in."""
import base64
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from ig import views
from ig.server import Handler
from test_reel_navigation import find_chromium


def picture(colour):
    svg = f"<svg xmlns='http://www.w3.org/2000/svg' width='400' height='500'><rect width='400' height='500' fill='{colour}'/></svg>"
    return 'data:image/svg+xml;base64,' + base64.b64encode(svg.encode()).decode()


POSTS = [
    {'shortcode': 'CAROUSEL01', 'url': 'https://www.instagram.com/p/CAROUSEL01/', 'image': None,
     'is_video': False, 'is_carousel': True, 'carousel_count': 3, 'like_count': 5, 'comment_count': 1,
     'taken_at': 1760000000, 'caption': 'three pictures', 'owner': 'someone'},
    {'shortcode': 'PHOTO00001', 'url': 'https://www.instagram.com/p/PHOTO00001/', 'image': picture('gray'),
     'is_video': False, 'is_carousel': False, 'like_count': 2, 'comment_count': 0,
     'taken_at': 1760000000, 'caption': 'one picture', 'owner': 'someone'},
]
MEDIA = {'CAROUSEL01': {'slides': [{'image': picture('red')}, {'image': picture('green')}, {'image': picture('blue')}],
                        'video': None, 'poster': picture('red')}}


class CarouselHandler(Handler):
    def do_GET(self):
        if self.path == '/test-profile':
            html = views.viewer_html('@someone', 'posts', POSTS, username='someone')
            return self._send(200, html.encode(), 'text/html; charset=utf-8')
        return super().do_GET()

    def do_POST(self):
        if self.path == '/test-profile/media':
            code = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}').get('code')
            if code in MEDIA:
                return self._send(200, json.dumps(MEDIA[code]).encode(), 'application/json')
            return self._send(502, b'{"error":"Instagram is unavailable"}', 'application/json')
        return super().do_POST()


class ProfileCarouselTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is needed to drive the browser')
    def test_swipe_counter_dots_and_previews(self):
        chromium = find_chromium()
        if not chromium:
            self.skipTest('no headless Chromium found')
        with tempfile.TemporaryDirectory() as directory, patch('ig.config.CONFIG_DIR', Path(directory)), \
             patch.object(CarouselHandler, '_allowed', return_value=True):
            server = ThreadingHTTPServer(('127.0.0.1', 0), CarouselHandler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                result = subprocess.run(['node', str(Path(__file__).with_name('profile_carousel.cjs')), chromium,
                                         f'http://127.0.0.1:{server.server_address[1]}/test-profile'],
                                        capture_output=True, text=True, timeout=90)
                if result.returncode == 77:
                    self.skipTest('headless Chromium could not start: ' + result.stderr[-300:])
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            finally:
                server.shutdown()


class CarouselPreviewTests(unittest.TestCase):
    def test_multi_image_post_uses_first_item_when_it_has_no_preview(self):
        from ig import instagram
        node = {'code': 'CAROUSEL01', 'media_type': 8, 'carousel_media_count': 2,
                'carousel_media': [{'image_versions2': {'candidates': [{'url': 'https://cdn.test/first.jpg'}]}},
                                   {'display_uri': 'https://cdn.test/second.jpg'}]}
        self.assertEqual(instagram.slim(node)['image'], 'https://cdn.test/first.jpg')
        node['display_uri'] = 'https://cdn.test/cover.jpg'
        self.assertEqual(instagram.slim(node)['image'], 'https://cdn.test/cover.jpg', 'the post-level preview wins')
        self.assertIsNone(instagram.slim({'code': 'X', 'media_type': 8, 'carousel_media': []})['image'])
