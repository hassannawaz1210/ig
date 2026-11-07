import io
import json
from unittest.mock import MagicMock, patch
from ig import instagram, views, config
from ig.server import Handler
from test_profile_cache import TempDirs, profile_html


class ProfileViewTests(TempDirs):
    def test_profile_identity_uses_existing_payload_and_survives_cache(self):
        html = profile_html().replace('"biography": "space"',
            '"biography": "space", "full_name": "NASA", "follower_count": 123, "following_count": 4, "all_media_count": 88, "profile_pic_url": "https://cdninstagram.com/avatar.jpg"')
        with patch.object(instagram, 'fetch_profile_page', return_value=(MagicMock(), html)) as fetch:
            state = self.store.fetch('nasa')
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(state['profile']['followers'], 123)
        self.assertEqual(self.store.cache.load('nasa', 60)['profile'], state['profile'])

    def test_profile_markup_escapes_bio_and_omits_unsafe_links(self):
        html = views.viewer_html('@creator', 'Posts', [], 'creator', profile={
            'full_name': '<script>bad</script>', 'biography': '<img onerror=bad>',
            'followers': 1200, 'external_url': 'javascript:alert(1)', 'avatar': 'javascript:alert(2)'})
        self.assertIn('1,200', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('javascript:', html)
        self.assertIn('profile-summary', html)
        self.assertIn('lb-comments-toggle', html)
        self.assertIn('/static/zoom.js', html)

    def handler(self):
        handler = Handler.__new__(Handler)
        handler.wfile = io.BytesIO()
        for name in ('send_response', 'send_header', 'end_headers'):
            setattr(handler, name, MagicMock())
        return handler

    def test_photo_download_uses_image_type_and_filename(self):
        handler = self.handler()
        response = MagicMock(status_code=200, headers={'Content-Type': 'image/jpeg'})
        response.iter_content.return_value = [b'photo bytes']
        with patch('ig.server.fetch_video', return_value=response):
            handler._download_progressive('ABCDE-2', {'image': 'https://cdninstagram.com/photo.jpg'})
        handler.send_header.assert_any_call('Content-Type', 'image/jpeg')
        handler.send_header.assert_any_call('Content-Disposition', 'attachment; filename="ABCDE-2.jpg"')
        self.assertEqual(handler.wfile.getvalue(), b'photo bytes')
        response.close.assert_called_once()

    def test_carousel_download_uses_only_requested_item(self):
        handler = self.handler()
        items = [{'image': 'one'}, {'video': 'two'}]
        with patch('ig.server.media_info', return_value={'slides': items}), \
             patch.object(handler, '_download_progressive') as download:
            handler._download('ABCDE', 1)
        download.assert_called_once_with('ABCDE-2', items[1])

    def test_download_rejects_non_media_image_response_and_non_cdn(self):
        handler = self.handler()
        response = MagicMock(status_code=200, headers={'Content-Type': 'text/html'})
        with patch('ig.server.fetch_video', return_value=response), patch.object(handler, '_send') as send:
            handler._download_progressive('ABCDE', {'image': 'https://cdninstagram.com/photo.jpg'})
        self.assertEqual(send.call_args.args[0], 502)
        with patch('ig.server.fetch_video') as fetch, patch.object(handler, '_send') as send:
            handler._download_progressive('ABCDE', {'image': 'http://localhost/private'})
        fetch.assert_not_called()
        self.assertEqual(send.call_args.args[0], 502)
