import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from ig import feeds, history


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        for target, value in [
            ('ig.config.CONFIG_DIR', Path(self.directory.name)),
            ('ig.history.HISTORY_JSON', Path(self.directory.name) / 'history.json'),
            ('ig.feeds.load_feeds', lambda: {'memes': ['a', 'b']}),
            ('ig.feeds.prefer_memes', lambda: False),
            ('ig.feeds.STATE', {owner: {'posts': [
                {'shortcode': f'{owner}{i}', 'owner': owner, 'is_video': True}
                for i in range(24)], 'has_more': False} for owner in ['a', 'b']}),
        ]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_fresh_pool_does_not_repeat_clips_or_opening_account(self):
        first = feeds.pool_more(feeds.make_pool(['a', 'b']))['posts']
        second = feeds.pool_more(feeds.make_pool(['a', 'b']))['posts']
        self.assertTrue(first and second)
        self.assertFalse({p['shortcode'] for p in first} & {p['shortcode'] for p in second})
        self.assertNotEqual(first[0]['owner'], second[0]['owner'])

    def test_simultaneous_requests_do_not_repeat_clips(self):
        pool = feeds.make_pool(['a', 'b'])
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: feeds.pool_more(pool)['posts'], range(2)))
        self.assertFalse({p['shortcode'] for p in results[0]} &
                         {p['shortcode'] for p in results[1]})

    def test_history_is_bounded(self):
        with patch('ig.history.MAX_HISTORY', 2):
            history.save_history({'codes': ['old', 'older'], 'last_owner': None},
                                 [{'shortcode': 'new', 'owner': 'a'}])
            self.assertEqual(history.load_history()['codes'], ['older', 'new'])

    def test_reads_past_already_served_pages(self):
        history.save_history({'codes': [], 'last_owner': None},
                             [{'shortcode': 'seen', 'owner': 'a'}])
        state = {'posts': [{'shortcode': 'seen', 'owner': 'a', 'is_video': True}],
                 'has_more': True}
        def next_page(username):
            state['posts'].append({'shortcode': 'fresh', 'owner': 'a', 'is_video': True})
            state['has_more'] = False
            return {'posts': state['posts'][-1:]}
        with patch('ig.feeds.STATE', {'a': state}), \
             patch('ig.feeds.load_feeds', return_value={'memes': ['a']}), \
             patch('ig.refill.more_posts', side_effect=next_page):
            pool = feeds.make_pool(['a'])
            result = feeds.pool_more(pool)
            for future, _ in list(feeds.REFILL.pending.values()):
                future.result(timeout=2)
            if not result['posts']:
                result = feeds.pool_more(pool)
        self.assertEqual([p['shortcode'] for p in result['posts']], ['fresh'])
