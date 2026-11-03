"""Browser check: one key press, wheel notch or extension scroll moves one reel.

Runs the real reels page with fake posts in headless Chromium. Skipped when
Node.js or a Chromium binary is not available. Set IG_TEST_CHROME to choose
the browser binary."""
import glob
import json
import os
import shutil
import subprocess
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ig import views

SKIPPED = 77  # reel_navigation.cjs could not start the browser


def find_chromium() -> str | None:
    if os.environ.get('IG_TEST_CHROME'):
        return os.environ['IG_TEST_CHROME']
    cache = Path.home() / '.cache' / 'ms-playwright'
    for pattern in ('chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell',
                    'chromium-*/chrome-linux64/chrome'):
        found = sorted(glob.glob(str(cache / pattern)))
        if found:
            return found[-1]
    for name in ('chromium', 'chromium-browser', 'google-chrome', 'chrome'):
        if shutil.which(name):
            return shutil.which(name)
    return None


def fake_posts(count: int, start: int = 0) -> list[dict]:
    return [{'shortcode': f'code{n:05d}', 'owner': f'creator{n % 5}', 'caption': f'reel {n}',
             'taken_at': 1700000000, 'like_count': n, 'comment_count': 0,
             'image': "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' width='9' height='16'/>"}
            for n in range(start, start + count)]


class FakeReelsServer(BaseHTTPRequestHandler):
    """The reels page and its static files, with stub JSON endpoints."""
    batches = 0
    selected = None
    deleted = None
    playback = {'sound': False, 'auto_scroll': False}
    feed_names = ['memes', 'saved', 'gym', 'programming', 'electronics', 'edits', 'gaming', 'islam']

    def log_message(self, *args):
        pass

    def reply(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/settings/data':
            return self.reply(200, json.dumps({'feeds':{name:[] for name in self.feed_names},
                'scroll_animation':'snappy', **self.playback}).encode(), 'application/json')
        if self.path == '/settings':
            return self.reply(200, views.render('settings.html', boot=views._page_data(
                {'android':False, 'scroll_animation':'snappy'})).encode(), 'text/html; charset=utf-8')
        if self.path == '/preferences':
            selection = self.selected
            if selection is not None:
                selection = [n for n in selection if n in self.feed_names]
            return self.reply(200, json.dumps({'feeds':self.feed_names,'selected_feeds':selection}).encode(), 'application/json')
        if self.path == '/feeds':
            return self.reply(200, json.dumps(self.feed_names).encode(), 'application/json')
        if self.path.startswith('/static/'):
            found = views.static_file(self.path.removeprefix('/static/'))
            if found is None:
                return self.reply(404, b'not found', 'text/plain')
            return self.reply(200, *found)
        if self.path in ('/reels', '/'):
            boot = views._page_data({'posts': fake_posts(12), 'feed_accounts': [], 'blocked': [],
                                     'selected_feeds': self.selected, **self.playback})
            choices = ''.join(f'<button type="button" class="feed-choice" data-feed="{name}" '
                              f'aria-pressed="false">{views.icon("check")}<span>{name}</span></button>'
                              for name in ['memes', 'saved', 'gym', 'programming', 'electronics', 'edits', 'gaming', 'islam'])
            page = views.render('reels.html', feed_choices=choices, boot=boot)
            return self.reply(200, page.encode(), 'text/html; charset=utf-8')
        self.reply(404, b'not found', 'text/plain')

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length') or 0))
        if self.path == '/settings/playback':
            self.playback.update({key:value for key,value in json.loads(body).items()
                                  if key in ('sound','auto_scroll')})
            data = self.playback
        elif self.path == '/reels/more':
            FakeReelsServer.batches += 1
            data = {'posts': fake_posts(12, 12 * FakeReelsServer.batches), 'has_more': True}
        elif self.path == '/preferences':
            data = json.loads(body)
            FakeReelsServer.selected = data['selected_feeds']
        elif self.path == '/feeds/delete':
            name = json.loads(body)['name']
            self.feed_names.remove(name)
            FakeReelsServer.deleted = name
            data = {'name':name, 'deleted':True, 'deletion_id':1}
        elif self.path == '/feeds/restore':
            self.feed_names.append(self.deleted)
            data = {'name':self.deleted,'restored':True}
        elif self.path == '/feeds':
            request = json.loads(body)
            name = request['name'].lower().replace(' ', '_')
            if name not in self.feed_names:
                self.feed_names.append(name)
            data = {'name': name, 'created': request.get('create', False),
                    'accounts': [a.lstrip('@') for a in request['accounts'].split()]}
        elif self.path == '/reels/media':
            data = {'error': 'no video in this test'}
        else:
            data = {'feedback': None}
        self.reply(200, json.dumps(data).encode(), 'application/json')


class ReelNavigationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is needed to drive the browser')
    def test_each_input_moves_one_reel(self):
        chromium = find_chromium()
        if not chromium:
            self.skipTest('no headless Chromium found (set IG_TEST_CHROME)')
        server = ThreadingHTTPServer(('127.0.0.1', 0), FakeReelsServer)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f'http://127.0.0.1:{server.server_address[1]}/reels'
        script = Path(__file__).with_name('reel_navigation.cjs')
        result = subprocess.run(['node', str(script), chromium, url],
                                capture_output=True, text=True, timeout=90)
        if result.returncode == SKIPPED:
            self.skipTest('headless Chromium could not start: ' + result.stderr.strip()[-300:])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
