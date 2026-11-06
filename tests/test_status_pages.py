import unittest
from unittest.mock import patch
from ig import views
from ig.server import Handler
from ig.instagram import ProfileUnavailable


class StatusPageTests(unittest.TestCase):
    def test_profile_failure_has_retry_and_back_without_exposing_html(self):
        handler = Handler.__new__(Handler)
        handler.path = '/u/missing'
        with patch('ig.server.load_profile', side_effect=ProfileUnavailable('<script>bad</script>')), patch.object(handler, '_send') as send:
            handler.do_GET()
        code, body, mime = send.call_args.args
        self.assertEqual(code, 404)
        self.assertIn('text/html', mime)
        self.assertIn(b'Account unavailable', body)
        self.assertIn(b'id="retry-page"', body)
        self.assertIn(b'Back to reels', body)
        self.assertNotIn(b'<script>bad', body)

    def test_json_errors_still_json(self):
        handler = Handler.__new__(Handler)
        handler.path = '/settings/data'
        with patch.object(handler, '_send') as send:
            handler._fail(ProfileUnavailable('Unavailable'), as_json=True)
        self.assertEqual(send.call_args.args[2], 'application/json')

    def test_update_fallback_has_download_and_back(self):
        handler = Handler.__new__(Handler)
        handler.path = '/app/update/check'
        with patch.object(handler, '_send') as send:
            handler.do_GET()
        code, body, mime = send.call_args.args
        self.assertEqual(code, 200)
        self.assertIn('text/html', mime)
        self.assertIn(b'releases/tag/android-updates', body)
        self.assertNotIn(b'id="retry-page"', body)

    def test_status_page_has_mobile_viewport(self):
        self.assertIn('width=device-width', views.status_html('Error', 'Try later'))
