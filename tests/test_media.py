import unittest
from unittest.mock import MagicMock
from ig.media import fetch_video


class MediaRedirectTests(unittest.TestCase):
    def test_instagram_redirect_is_followed(self):
        session = MagicMock()
        redirect = MagicMock(status_code=302, headers={'Location':'https://video.cdninstagram.com/reel.mp4'})
        video = MagicMock(status_code=200)
        session.get.side_effect = [redirect, video]
        self.assertIs(fetch_video(session, 'https://video.fbcdn.net/reel'), video)
        self.assertEqual(session.get.call_count, 2)
        redirect.close.assert_called_once()

    def test_external_redirect_is_not_followed(self):
        session = MagicMock()
        session.get.return_value = MagicMock(status_code=302, headers={'Location':'http://localhost/secret'})
        with self.assertRaises(ValueError):
            fetch_video(session, 'https://video.fbcdn.net/reel')
        self.assertEqual(session.get.call_count, 1)
