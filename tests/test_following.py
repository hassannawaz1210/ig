import io
import json
import unittest
import zipfile
import zlib
from unittest.mock import patch

from ig import config, following
from ig.server import Handler
from test_ig import ProjectTests


def export(*names):
    return json.dumps({'relationships_following': [
        {'title': name, 'string_list_data': [{'href': 'https://www.instagram.com/' + name,
                                             'value': name, 'timestamp': 123}]}
        for name in names]}).encode()


def archive(files):
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w', zipfile.ZIP_DEFLATED) as file:
        for name, content in files.items():
            file.writestr(name, content)
    return result.getvalue()


class FollowingParserTests(unittest.TestCase):
    def test_json_deduplicates_and_supports_href_without_value(self):
        data = json.loads(export('NASA', 'nasa', 'valid.name'))
        data['relationships_following'].append({'title': 'other_name', 'string_list_data': [
            {'href': 'https://www.instagram.com/_u/other_name/', 'timestamp': 123}]})
        self.assertEqual(following.parse_export(json.dumps(data).encode(), 'following.json'),
                         ['nasa', 'valid.name', 'other_name'])

    def test_html_uses_only_instagram_profile_links(self):
        data = b'''<h1>Following</h1><a href="https://www.instagram.com/_u/NASA">NASA</a>
            <a href="https://instagram.com/other/">Other name</a>
            <a href="https://evil.test/trap">bad</a><a href="https://instagram.com/p/clip/">post</a>'''
        self.assertEqual(following.parse_export(data, 'following.html'), ['nasa', 'other'])
        with self.assertRaises(ValueError):
            following.parse_export(data, 'followers.html')

    def test_zip_reads_only_following_files(self):
        data = archive({'connections/followers_and_following/following.json': export('one'),
                        'connections/followers_and_following/following_2.json': export('two', 'one'),
                        'followers_1.json': export('not_following'),
                        'close_friends.json': export('not_following_either'),
                        'following_notes.json': export('unrelated')})
        self.assertEqual(following.parse_export(data, 'instagram.zip'), ['one', 'two'])

    def test_zip_never_extracts_paths(self):
        data = archive({'../../following.json': export('one')})
        self.assertEqual(following.parse_export(data), ['one'])

    def test_bad_or_empty_exports(self):
        for data, filename in [(b'{}', 'following.json'), (b'[]', 'followers.json'),
                               (b'not json', 'following.json'), (export(), 'following.json'),
                               (b'PKbroken', 'export.zip'),
                               (archive({'followers.json': export('one')}), 'export.zip')]:
            with self.subTest(filename=filename, data=data[:10]), self.assertRaises(ValueError):
                following.parse_export(data, filename)

    def test_limits_upload_expansion_and_account_count(self):
        with patch.object(following, 'MAX_UPLOAD', 3), self.assertRaises(following.TooLarge):
            following.parse_export(b'1234')
        with patch.object(following, 'MAX_CONTENT', 5), self.assertRaises(following.TooLarge):
            following.parse_export(archive({'following.json': export('one')}), 'export.zip')
        with patch.object(following, 'MAX_ACCOUNTS', 1), self.assertRaises(following.TooLarge):
            following.parse_export(export('one', 'two'))


class FollowingRouteTests(ProjectTests):
    def request(self, data, path='/following/import', filename='', size=None, allowed=True):
        handler = Handler.__new__(Handler)
        handler.path = path
        if not isinstance(data, bytes):
            data = json.dumps(data).encode()
        handler.headers = {'Content-Length': str(len(data) if size is None else size),
                           'X-File-Name': filename}
        handler.rfile = io.BytesIO(data)
        with patch.object(handler, '_send') as send, patch.object(handler, '_allowed', return_value=allowed):
            handler.do_POST()
        if not send.called:
            return None
        return send.call_args.args[0], json.loads(send.call_args.args[1])

    def test_preview_is_read_only_and_requires_access(self):
        config.save_feeds({'keep': ['old']})
        status, body = self.request(export('new'), '/following/preview', 'following.json')
        self.assertEqual((status, body), (200, {'accounts': ['new']}))
        self.assertEqual(config.load_feeds(), {'keep': ['old']})
        self.assertIsNone(self.request(export('new'), '/following/preview', allowed=False))
        self.assertIsNone(self.request({'name': 'new', 'create': True, 'accounts': ['x']}, allowed=False))
        self.assertEqual(config.load_feeds(), {'keep': ['old']})

    def test_large_import_is_atomic_idempotent_and_preserves_blocks(self):
        config.save_feeds({'keep': ['old'], 'imported': ['already']})
        config.set_blocked('blocked', True)
        accounts = ['already', 'blocked'] + ['account' + str(n) for n in range(250)]
        status, result = self.request({'name': 'imported', 'accounts': accounts})
        self.assertEqual(status, 200)
        self.assertEqual((result['added'], result['existing'], result['blocked']), (250, 1, 1))
        self.assertEqual(config.blocked_accounts(), {'blocked'})
        self.assertEqual(config.load_feeds()['keep'], ['old'])
        self.assertNotIn('blocked', config.load_feeds()['imported'])
        status, result = self.request({'name': 'imported', 'accounts': accounts})
        self.assertEqual((result['added'], result['existing'], result['blocked']), (0, 251, 1))

    def test_create_and_invalid_requests_do_not_partially_write(self):
        self.assertEqual(self.request({'name': 'My Feed', 'create': True,
                                       'accounts': ['NASA', 'nasa']})[0], 200)
        expected = {'my_feed': ['nasa']}
        self.assertEqual(config.load_feeds(), expected)
        cases = [({'name': 'my_feed', 'create': True, 'accounts': ['new']}, 409),
                 ({'name': 'missing', 'accounts': ['new']}, 400),
                 ({'name': 'new', 'create': True, 'accounts': ['valid', 'bad/name']}, 400),
                 ({'name': 'new', 'create': True, 'accounts': []}, 400),
                 ({'name': 'new', 'create': True, 'accounts': ['x'] * 7501}, 413)]
        for body, expected_status in cases:
            self.assertEqual(self.request(body)[0], expected_status)
            self.assertEqual(config.load_feeds(), expected)

    def test_corrupt_compressed_zip_returns_readable_error(self):
        data = archive({'following.json': export('one')})
        with patch('zipfile.ZipExtFile.read', side_effect=zlib.error('invalid distance code')):
            status, body = self.request(data, '/following/preview', 'export.zip')
        self.assertEqual(status, 400)
        self.assertIn('Could not read this ZIP', body['error'])

    def test_invalid_sizes_and_malformed_uploads(self):
        self.assertEqual(self.request(b'', '/following/preview', size=following.MAX_UPLOAD + 1)[0], 413)
        self.assertEqual(self.request(b'', '/following/preview', size=-1)[0], 400)
        self.assertEqual(self.request(b'{}', '/following/preview', size=10)[0], 400)
        self.assertEqual(self.request(b'bad', '/following/preview')[0], 400)
