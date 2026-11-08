"""Feeds page topic tools in a real browser: suggestions per feed, topic
search, and the trending strip with its sheet. The topic routes are stubbed
here; saving topics uses the real API with an isolated profile."""
import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from ig import config
from ig.server import Handler
from test_reel_navigation import find_chromium


def topic(name, word, count=None, image=None, **extra):
    return {'name': name, 'slug': word.replace(' ', '-'), 'word': word, 'count_label': count, 'image': image, **extra}


PICTURE = 'https://example.invalid/edit.jpg'
TRENDING = {'sections': [
    {'title': 'Trending now', 'topics': [
        topic('Edit', 'edit', '454M posts', PICTURE),
        topic('Gym', 'gym', '1,234 posts', 'http://example.invalid/gym.jpg'),
        topic('<img src=x onerror=alert(1)>', 'odd', None),
    ]},
    {'title': 'Music', 'topics': [topic('Phonk', 'phonk', '9.1M posts')]},
], 'creators': [], 'fetched_at': 1760000000}
SUGGESTIONS = {
    'edits': [topic('Movie edits', 'movie edits', '12.5M posts', reason='related'),
              topic('Anime', 'anime', '454M posts', reason='hashtag'),
              topic('Cars', 'cars', None, reason='trending')],
    'memes': [topic('Funny', 'funny', '88M posts', reason='related')],
}
SEARCH = {
    'ed': [topic('Edging', 'edging', '1M posts')],
    'edit': [topic('Edit', 'edit', '454M posts'), topic('Movie', 'movie', '3,400 posts')],
}


class TopicsHandler(Handler):
    state = {}
    log = []

    def reply(self, code, body, ctype='application/json'):
        try:
            return self._send(code, body if isinstance(body, bytes) else json.dumps(body).encode(), ctype)
        except (BrokenPipeError, ConnectionResetError):
            pass   # the page cancelled this request (an outdated search)

    def do_GET(self):
        url = urlsplit(self.path)
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        state = type(self).state
        if url.path == '/test/log':
            return self.reply(200, type(self).log)
        if url.path in ('/topics/trending', '/topics/search', '/feeds/topics/suggest'):
            type(self).log.append({'path': url.path, **query})
        if url.path == '/topics/trending':
            mode = state.get('trending', 'ok')
            if mode == 'fail':
                return self.reply(503, {'error': 'Instagram is unavailable'})
            return self.reply(200, {'sections': [], 'creators': [], 'fetched_at': 0} if mode == 'empty' else TRENDING)
        if url.path == '/feeds/topics/suggest':
            time.sleep(0.3)   # long enough to see the loading chips
            if state.get('suggest') == 'fail':
                return self.reply(502, b'<html>bad gateway</html>', 'text/html')
            return self.reply(200, {'suggestions': SUGGESTIONS.get(query.get('feed'), [])})
        if url.path == '/topics/search':
            q = query.get('q', '')
            if q == 'fail':
                return self.reply(500, {'error': 'Search failed'})
            if q == 'ed':
                time.sleep(0.7)   # answers after the newer search
            return self.reply(200, {'topics': SEARCH.get(q, [])})
        if url.path.startswith('/topic/'):
            return self.reply(200, b'<!doctype html><title>Topic page</title><h1>Topic page</h1>', 'text/html; charset=utf-8')
        return super().do_GET()

    def do_POST(self):
        if self.path == '/test/state':
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
            type(self).state.update(body)
            return self.reply(200, type(self).state)
        return super().do_POST()


class FeedTopicsPageTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is needed to drive the browser')
    def test_suggestions_search_and_trending(self):
        chromium = find_chromium()
        if not chromium:
            self.skipTest('no headless Chromium found')
        TopicsHandler.state, TopicsHandler.log = {}, []
        with tempfile.TemporaryDirectory() as directory, patch('ig.config.CONFIG_DIR', Path(directory)), \
             patch.object(TopicsHandler, '_allowed', return_value=True):
            config.save_feeds({'edits': ['alpha'], 'memes': ['shared']})
            config.set_feed_topic_words('edits', ['movie'])
            server = ThreadingHTTPServer(('127.0.0.1', 0), TopicsHandler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                result = subprocess.run(['node', str(Path(__file__).with_name('feed_topics_ui.cjs')), chromium,
                                         f'http://127.0.0.1:{server.server_address[1]}'],
                                        capture_output=True, text=True, timeout=120)
                if result.returncode == 77:
                    self.skipTest('headless Chromium could not start: ' + result.stderr[-300:])
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(config.feed_topic_words(),
                                 {'edits': ['movie', 'anime', 'edit', 'fail'], 'memes': ['edit', 'funny']})
            finally:
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    unittest.main()
