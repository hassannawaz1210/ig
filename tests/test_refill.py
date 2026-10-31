import threading
import unittest
from unittest.mock import patch
from ig.refill import Refill
from ig import feeds
from test_profile_cache import TempDirs
from ig import config


class BackgroundRefillTests(TempDirs):
    def setUp(self):
        super().setUp()
        self.refill = Refill()
        self.addCleanup(self.refill.executor.shutdown, wait=True)

    def test_slow_network_does_not_block_batch_and_requests_are_deduplicated(self):
        config.save_feeds({'memes':['cold']})
        release = threading.Event()
        entered = threading.Event()
        states = {}
        def load(name):
            entered.set()
            release.wait(2)
            states[name] = {'posts':[{'shortcode':'new001','owner':name,'is_video':True}], 'has_more':False}
            return states[name], True
        self.addCleanup(release.set)
        with patch('ig.feeds.REFILL',self.refill), patch('ig.feeds.STATE',states), \
             patch.object(feeds.PROFILES,'cached',return_value=None), \
             patch.object(feeds.PROFILES,'load',side_effect=load) as network:
            pool=feeds.make_pool(['cold'])
            result=feeds.pool_more(pool)
            self.assertTrue(entered.wait(1))
            self.assertFalse(release.is_set())
            self.assertEqual(result['posts'],[])
            self.assertTrue(result['has_more'])
            feeds.pool_more(pool)
            self.assertEqual(network.call_count,1)
            release.set()
            for future,_ in self.refill.pending.values(): future.result(timeout=2)
            result=feeds.pool_more(pool)
            self.assertEqual([p['shortcode'] for p in result['posts']],['new001'])

    def test_exhausted_sources_do_not_promise_another_batch(self):
        config.save_feeds({'memes':['empty']})
        states={'empty':{'posts':[], 'has_more':False}}
        with patch('ig.feeds.REFILL',self.refill), patch('ig.feeds.STATE',states):
            result=feeds.pool_more(feeds.make_pool(['empty']))
        self.assertEqual(result['posts'],[])
        self.assertFalse(result['has_more'])
        self.assertFalse(self.refill.pending)

    def test_pagination_starts_before_the_last_clips_are_delivered(self):
        state={'posts':[{'shortcode':str(i),'is_video':True} for i in range(10)],'has_more':True}
        with patch('ig.refill.more_posts',return_value={}) as paging:
            self.refill.schedule({'served':set()}, ['creator'], {'creator'}, states={'creator':state})
            for future,_ in self.refill.pending.values(): future.result(timeout=2)
        paging.assert_called_once_with('creator')

    def test_discovery_does_not_delay_replenishing_saved_sources(self):
        state = {'posts': [{'shortcode': str(i), 'is_video': True} for i in range(10)],
                 'has_more': True}
        with patch('ig.refill.PROFILES.cached', return_value=None), \
             patch('ig.refill.PROFILES.load', return_value=({}, False)), \
             patch('ig.refill.more_posts', return_value={}) as paging:
            self.refill.schedule({'served': set()}, ['trial1', 'trial2', 'saved'], {'saved'},
                                 states={'saved': state})
            for future, _ in self.refill.pending.values():
                future.result(timeout=2)
        paging.assert_called_once_with('saved')
        self.assertEqual(len(self.refill.pending), 2)

    def test_full_source_does_not_fetch_unneeded_pages(self):
        state = {'posts': [{'shortcode': str(i), 'is_video': True} for i in range(12)],
                 'has_more': True}
        with patch('ig.refill.more_posts') as paging:
            self.refill.schedule({'served': set()}, ['saved'], {'saved'}, states={'saved': state})
        paging.assert_not_called()
