import io
import unittest
from unittest.mock import MagicMock, patch
from ig.server import Handler


class DownloadTests(unittest.TestCase):
    def handler(self):
        handler = Handler.__new__(Handler)
        handler.wfile = io.BytesIO()
        for name in ['send_response', 'send_header', 'end_headers', '_send']:
            setattr(handler, name, MagicMock())
        return handler

    def test_video_attachment_and_bytes(self):
        handler = self.handler()
        response = MagicMock(status_code=200)
        response.iter_content.return_value = [b'video', b'data']
        with patch('ig.server.media_info', return_value={'video': 'https://video.cdninstagram.com/file.mp4'}), \
             patch('ig.server.cr.Session') as session:
            session.return_value.__enter__.return_value.get.return_value = response
            handler._download('abcde123')
        handler.send_response.assert_called_once_with(200)
        handler.send_header.assert_any_call('Content-Disposition', 'attachment; filename="abcde123.mp4"')
        self.assertEqual(handler.wfile.getvalue(), b'videodata')
        response.close.assert_called_once()
        handler._send.assert_not_called()

    def test_missing_video_and_invalid_host(self):
        for data, status in [({}, 404), ({'video': 'https://example.com/video'}, 502)]:
            handler = self.handler()
            with patch('ig.server.media_info', return_value=data):
                handler._download('abcde123')
            self.assertEqual(handler._send.call_args.args[0], status)

    def test_invalid_post_code(self):
        handler = self.handler()
        handler.path = '/download/../secret'
        with patch('ig.server.media_info') as media:
            handler.do_GET()
        media.assert_not_called()
        self.assertEqual(handler._send.call_args.args[0], 400)
