import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ig import recognition_cache as cache


class RecognitionCacheTests(unittest.TestCase):
    def test_matches_no_matches_and_failures(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(cache, 'DATABASE', Path(directory) / 'songs.sqlite3'):
            self.assertIsNone(cache.cached_song('song'))
            match = {'status': 'matched', 'title': 'Song', 'artist': 'Artist'}
            cache.cache_song('song', match)
            self.assertEqual(cache.cached_song('song'), match)
            cache.cache_song('silent', {'status': 'no_match'})
            self.assertEqual(cache.cached_song('silent'), {'status': 'no_match'})
            cache.cache_song('failure', {'status': 'error'})
            self.assertIsNone(cache.cached_song('failure'))
            with patch.object(cache.time, 'time', return_value=cache.time.time() + cache.NO_MATCH_TTL + 1):
                self.assertIsNone(cache.cached_song('silent'))
                self.assertEqual(cache.cached_song('song'), match)
