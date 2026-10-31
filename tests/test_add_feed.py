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


class ManageFeedTests(ProjectTests):
    def request_feed(self, body):
        handler = Handler.__new__(Handler)
        handler.path = '/feeds'
        data = json.dumps(body).encode()
        handler.headers = {'Content-Length': str(len(data))}
        handler.rfile = io.BytesIO(data)
        with patch.object(handler, '_send') as send:
            handler.do_POST()
        return send.call_args.args[0], json.loads(send.call_args.args[1])

    def test_create_and_bulk_add_preserve_existing_data(self):
        config.save_feeds({'mine': ['keep']})
        config.set_selected_feeds(['mine'])
        config.set_blocked('blocked', True)
        status, result = self.request_feed({'name': 'Car edits', 'create': True,
            'accounts': '@Alpha, https://www.instagram.com/Beta/?utm_source=test\nalpha'})
        self.assertEqual(status, 200)
        self.assertEqual(result['accounts'], ['alpha', 'beta'])
        self.assertEqual(config.load_feeds(), {'mine': ['keep'], 'car_edits': ['alpha', 'beta']})
        self.assertEqual(config.selected_feeds(), ['mine'])
        self.assertEqual(config.blocked_accounts(), {'blocked'})
        self.assertEqual(self.request_feed({'name':'car_edits', 'accounts':'@gamma @alpha'})[0], 200)
        self.assertEqual(config.load_feeds()['car_edits'], ['alpha', 'beta', 'gamma'])

    def test_empty_feed_and_duplicate_creation(self):
        self.assertEqual(self.request_feed({'name':'empty', 'create':True})[0], 200)
        self.assertEqual(self.request_feed({'name':'empty', 'create':True, 'accounts':'oops'})[0], 409)
        self.assertEqual(config.load_feeds(), {'empty':[]})

    def test_invalid_entries_never_partially_write(self):
        for body in [
            {'name':'bad/name', 'create':True},
            {'name':'new', 'create':True, 'accounts':'good https://evil.example/user'},
            {'name':'new', 'create':True, 'accounts':'https://instagram.com/p/clip/'},
            {'name':'new', 'accounts':'user'},
            {'name':'new', 'create':True, 'accounts':['user']},
        ]:
            with self.subTest(body=body):
                self.assertEqual(self.request_feed(body)[0], 400)
                self.assertEqual(config.load_feeds(), {})

    def test_feeds_endpoint_has_no_implicit_saved_feed(self):
        handler = Handler.__new__(Handler)
        handler.path = '/feeds'
        with patch.object(handler, '_send') as send:
            handler.do_GET()
        self.assertEqual(json.loads(send.call_args.args[1]), [])
