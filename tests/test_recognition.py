import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from ig import recognition


class RecognitionTests(unittest.TestCase):
    def test_normalizes_match_and_no_match(self):
        self.assertEqual(recognition.track_result({}), {'status': 'no_match'})
        result = recognition.track_result({'track': {'title':'Song', 'subtitle':'Artist', 'url':'https://shazam.com/song'}})
        self.assertEqual(result, {'status':'matched','title':'Song','artist':'Artist','url':'https://shazam.com/song'})
        self.assertEqual(recognition.track_result({'track': {'title':'Song', 'url':'javascript:bad'}})['url'], '')

    def test_cache_avoids_network(self):
        with patch.object(recognition, 'cached_song', return_value={'status':'no_match'}), \
             patch.object(recognition, 'media_info') as media:
            self.assertEqual(recognition.recognize_song('abcde'), {'status':'no_match'})
            media.assert_not_called()

    def test_download_fingerprint_and_cache(self):
        response = MagicMock(status_code=200)
        response.iter_content.return_value = [b'media bytes']
        with patch.object(recognition, 'cached_song', return_value=None), \
             patch.object(recognition, 'cache_song') as cache, \
             patch.object(recognition, 'media_info', return_value={'video':'https://video.cdninstagram.com/reel.mp4'}), \
             patch.object(recognition.cr, 'Session') as session, \
             patch.object(recognition, 'identify_file', new_callable=AsyncMock) as identify:
            session.return_value.__enter__.return_value.get.return_value = response
            identify.return_value = {'track': {'title': 'Song', 'subtitle': 'Artist'}}
            result = recognition.recognize_song('abcde')
            self.assertEqual(result['title'], 'Song')
            cache.assert_called_once_with('abcde', result)
            self.assertFalse(identify.call_args.args[0].exists(), 'temporary video should be removed')
        response.close.assert_called_once()

    def test_service_failure_is_not_cached_as_no_match(self):
        with patch.object(recognition, 'cached_song', return_value=None), \
             patch.object(recognition, 'media_info', side_effect=RuntimeError('offline')), \
             patch.object(recognition, 'cache_song') as cache:
            self.assertEqual(recognition.recognize_song('abcde'), {'status':'unavailable'})
            cache.assert_not_called()
