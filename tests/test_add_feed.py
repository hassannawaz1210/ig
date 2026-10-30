import io
import json
from unittest.mock import patch
from test_ig import ProjectTests
from ig import config
from ig.server import Handler


class AddFeedTests(ProjectTests):
    def request(self, body):
        handler = Handler.__new__(Handler)
        handler.path = '/add'
        data = json.dumps(body).encode()
        handler.headers = {'Content-Length': str(len(data))}
        handler.rfile = io.BytesIO(data)
        with patch.object(handler, '_send') as send:
            handler.do_POST()
        return send.call_args.args[0]

    def test_add_uses_explicit_feed_and_is_idempotent(self):
        config.save_feeds({'memes': [], 'edits': []})
        for _ in range(2):
            self.assertEqual(self.request({'username': 'creator', 'feed': 'edits'}), 200)
        self.assertEqual(config.load_feeds(), {'memes': [], 'edits': ['creator']})

    def test_missing_and_unknown_feed_do_not_write(self):
        config.save_feeds({'memes': []})
        for body in [{'username': 'creator'}, {'username': 'creator', 'feed': 'unknown'}]:
            self.assertEqual(self.request(body), 400)
        self.assertEqual(config.load_feeds(), {'memes': []})

    def test_picker_shows_saved_membership_and_counts(self):
        config.save_feeds({'memes': ['ishowspeed'], 'edits': []})
        handler = Handler.__new__(Handler)
        handler.path = '/feeds?username=ishowspeed'
        with patch.object(handler, '_send') as send:
            handler.do_GET()
        rows = json.loads(send.call_args.args[1])
        self.assertEqual(next(row for row in rows if row['name'] == 'memes'),
                         {'name': 'memes', 'count': 1, 'saved': True})
        self.assertFalse(next(row for row in rows if row['name'] == 'edits')['saved'])
