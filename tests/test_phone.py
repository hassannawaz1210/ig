import contextlib
import http.client
import io
import logging
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from ig import cli, config, logs, phone
from ig.instagram import RateLimited
from ig.server import make_server


class TempConfig(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name)
        for target, value in [('ig.config.CONFIG_DIR', path / 'config'), ('ig.config.CACHE', path / 'cache')]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)


class LiveServer(TempConfig):
    """A real server on a free local port, as a phone would see it."""
    access = None

    def setUp(self):
        super().setUp()
        self.server = make_server('127.0.0.1', 0, self.access)
        thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()


class PhoneAccessTests(LiveServer):
    def setUp(self):
        self.access = phone.PhoneAccess('abcd2345')  # Fresh lockout and device memory per test.
        super().setUp()

    def test_pages_ask_for_the_code_first(self):
        status, _, body = self.request('GET', '/u/nasa')
        self.assertEqual(status, 401)
        self.assertIn(b'name="code"', body)
        self.assertIn(b'value="/u/nasa"', body)
        self.assertEqual(self.request('GET', '/static/reels.css')[0], 401)
        self.assertEqual(self.request('POST', '/feedback', b'{}')[0], 401)

    def test_ping_needs_no_code(self):
        self.assertEqual(self.request('GET', '/ping')[0], 200)

    def test_link_with_key_sets_a_cookie_and_hides_the_key(self):
        with self.assertLogs('ig.phone', 'INFO'):
            status, headers, _ = self.request('GET', '/?key=ABCD2345')
        self.assertEqual(status, 303)
        self.assertEqual(headers['Location'], '/')
        self.assertIn('ig_phone=abcd2345', headers['Set-Cookie'])
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        status, _, body = self.request('GET', '/static/reels.css', headers={'Cookie': 'ig_phone=abcd2345'})
        self.assertEqual(status, 200)
        self.assertIn(b'.slide', body)

    def test_code_form_signs_in_and_returns_to_the_page(self):
        with self.assertLogs('ig.phone', 'INFO'):
            status, headers, _ = self.request('POST', '/sign-in', b'code=abcd2345&next=%2Fu%2Fnasa',
                                              {'Content-Type': 'application/x-www-form-urlencoded'})
        self.assertEqual((status, headers['Location']), (303, '/u/nasa'))

    def test_sign_in_never_redirects_to_another_site(self):
        with self.assertLogs('ig.phone', 'INFO'):
            _, headers, _ = self.request('POST', '/sign-in', b'code=abcd2345&next=%2F%2Fevil.example')
        self.assertEqual(headers['Location'], '/')

    def test_wrong_codes_are_limited(self):
        with self.assertLogs('ig.phone', 'WARNING'):
            for _ in range(phone.PhoneAccess.MAX_FAILURES):
                status, _, body = self.request('POST', '/sign-in', b'code=wrong')
                self.assertEqual(status, 401)
                self.assertIn(b'not right', body)
        status, _, body = self.request('POST', '/sign-in', b'code=abcd2345')
        self.assertEqual(status, 429, 'even the right code waits during a lockout')
        self.assertIn(b'Too many', body)

    def test_wrong_cookie_is_rejected(self):
        self.assertEqual(self.request('GET', '/blocked', headers={'Cookie': 'ig_phone=nope'})[0], 401)


class ServerErrorTests(LiveServer):
    def test_unexpected_errors_are_logged_but_not_shown(self):
        with patch('ig.server.load_profile', side_effect=KeyError('secret internal detail')), \
                self.assertLogs('ig.server', 'ERROR') as logged:
            status, _, body = self.request('GET', '/u/nasa')
        self.assertEqual(status, 500)
        self.assertNotIn(b'secret', body)
        self.assertIn(b'Try again', body)
        self.assertIn(b'Back to reels', body)
        self.assertIn('secret internal detail', '\n'.join(logged.output))

    def test_instagram_errors_keep_their_helpful_message(self):
        with patch('ig.server.load_profile', side_effect=RateLimited('Instagram is rate limiting requests')), \
                self.assertLogs('ig.server', 'INFO'):
            status, _, body = self.request('GET', '/u/nasa')
        self.assertEqual(status, 429)
        self.assertIn(b'rate limiting', body)

    def test_static_files_are_limited_to_the_static_folder(self):
        self.assertEqual(self.request('GET', '/static/reels.js')[0], 200)
        self.assertEqual(self.request('GET', '/static/icons.js')[0], 200)
        for path in ('/static/../views.py', '/static/reels.html', '/static/missing.js', '/static/%2e%2e/cli.py'):
            self.assertEqual(self.request('GET', path)[0], 404, path)

    def test_local_server_needs_no_code(self):
        self.assertEqual(self.request('GET', '/static/reels.css')[0], 200)


class PhoneCommandTests(TempConfig):
    def test_code_is_kept_until_renewed(self):
        first = phone.AccessCode.current()
        self.assertEqual(len(first), phone.AccessCode.LENGTH)
        self.assertTrue(set(first) <= set(phone.AccessCode.ALPHABET))
        self.assertEqual(phone.AccessCode.current(), first)
        self.assertEqual(config.text_preference(phone.AccessCode.SETTING), first)
        self.assertNotEqual(phone.AccessCode.current(renew=True), first)

    def test_instructions_show_link_and_code(self):
        text = phone.instructions('192.168.1.5', 7350, 'abcd2345')
        self.assertIn('http://192.168.1.5:7350/?key=abcd2345', text)
        self.assertIn('type the code:  abcd2345', text)
        self.assertIn('Ctrl+C', text)

    def test_no_network_gives_a_clear_message(self):
        stderr = io.StringIO()
        with patch('ig.phone.network_address', return_value=None), contextlib.redirect_stderr(stderr):
            self.assertEqual(phone.share(), 1)
        self.assertIn('Wi-Fi', stderr.getvalue())

    def test_running_share_is_reused_not_duplicated(self):
        stdout = io.StringIO()
        with patch('ig.phone.network_address', return_value='192.168.1.5'), \
                patch('ig.phone._already_running', return_value=True), \
                patch('ig.server.make_server') as make, contextlib.redirect_stdout(stdout):
            self.assertEqual(phone.share(), 0)
        make.assert_not_called()
        self.assertIn('already running', stdout.getvalue())

    def test_bad_options_are_explained(self):
        for args, message in ((['phone', '--port', 'abc'], 'needs a number'),
                              (['phone', '--port', '99999'], 'between'),
                              (['phone', '--qr'], 'unknown option')):
            with self.assertRaisesRegex(SystemExit, message):
                cli.main(args)

    def test_help(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            cli.main(['phone', '--help'])
        self.assertIn('--new-code', stdout.getvalue())


class LogTests(TempConfig):
    def test_server_log_is_written_and_shown(self):
        root = logging.getLogger('ig')
        before = list(root.handlers)
        self.addCleanup(lambda: [root.removeHandler(h) or h.close() for h in root.handlers if h not in before])
        self.addCleanup(setattr, root, '_ig_configured', False)
        self.addCleanup(root.setLevel, root.level)
        logs.setup()
        logging.getLogger('ig.test').warning('something to see')
        self.assertTrue(any('something to see' in line for line in logs.tail()))
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            cli.main(['logs'])
        self.assertIn('something to see', stdout.getvalue())


if __name__ == '__main__':
    unittest.main()
