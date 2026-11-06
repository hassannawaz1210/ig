import threading
import random
import unittest
from unittest.mock import patch, MagicMock
from ig.refill import Refill
from ig import feeds
from test_profile_cache import TempDirs
from ig import config


class BackgroundRefillTests(TempDirs):
    def setUp(self):
        super().setUp()
        topic_pages = patch('ig.feeds.TOPICS.enabled', False)
        topic_pages.start()
        self.addCleanup(topic_pages.stop)
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
             patch.object(feeds.REELS,'cached',return_value=None), \
             patch.object(feeds.REELS,'load',side_effect=load) as network:
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
        with patch('ig.refill.more_reels',return_value={}) as paging:
            self.refill.schedule({'served':set()}, ['creator'], {'creator'}, states={'creator':state})
            for future,_ in self.refill.pending.values(): future.result(timeout=2)
        paging.assert_called_once_with('creator')

    def test_discovery_does_not_delay_replenishing_saved_sources(self):
        state = {'posts': [{'shortcode': str(i), 'is_video': True} for i in range(10)],
                 'has_more': True}
        with patch('ig.refill.REELS.cached', return_value=None), \
             patch('ig.refill.REELS.load', return_value=({}, False)), \
             patch('ig.refill.more_reels', return_value={}) as paging:
            self.refill.schedule({'served': set()}, ['trial1', 'trial2', 'saved'], {'saved'},
                                 states={'saved': state})
            for future, _ in self.refill.pending.values():
                future.result(timeout=2)
        paging.assert_called_once_with('saved')
        self.assertEqual(len(self.refill.pending), 2)

    def test_full_source_does_not_fetch_unneeded_pages(self):
        state = {'posts': [{'shortcode': str(i), 'is_video': True} for i in range(12)],
                 'has_more': True}
        with patch('ig.refill.more_reels') as paging:
            self.refill.schedule({'served': set()}, ['saved'], {'saved'}, states={'saved': state})
        paging.assert_not_called()

    def test_empty_early_accounts_cannot_starve_other_saved_accounts(self):
        names = ['adam', 'aradz', 'dzividin', 'film', 'woyshnis']
        states = {name:{'posts':[], 'has_more':True} for name in names}
        loaded = []
        with patch('ig.refill.more_reels', side_effect=lambda name: loaded.append(name)):
            for _ in range(3):
                self.refill.schedule({'served':set()}, names, set(names), states=states)
                for future, _ in self.refill.pending.values():
                    future.result(timeout=2)
                self.refill.collect({}, lambda *args: None)
        self.assertEqual(set(loaded), set(names))
        self.assertEqual(len(set(loaded[:4])), 4)

    def test_cold_workers_vary_without_expanding_concurrency_or_ignoring_wait_order(self):
        names = ['alpha', 'bravo', 'charlie', 'delta', 'echo', 'foxtrot']
        starts = []
        with patch('ig.refill.REELS.cached', return_value=None), \
             patch.object(self.refill.executor, 'submit', return_value=MagicMock()):
            for seed in (1, 2, 3):
                self.refill.pending.clear()
                self.refill.last_scheduled.clear()
                with patch('ig.refill.random.shuffle', side_effect=random.Random(seed).shuffle):
                    self.refill.schedule({'served': set()}, names, set(names), states={})
                starts.append(tuple(self.refill.pending))
                self.assertEqual(len(self.refill.pending), 2)
            self.assertGreater(len(set(starts)), 1)
            self.assertTrue(any(set(start) != set(names[:2]) for start in starts))
            # Never scheduled creators still precede previously loaded sources,
            # regardless of how the initial tie was randomized.
            self.refill.pending.clear()
            self.refill.last_scheduled = {name: 100 for name in names[2:]}
            self.refill.schedule({'served': set()}, names, set(names), states={})
            self.assertEqual(set(self.refill.pending), set(names[:2]))
            self.refill.pending.clear()
            self.refill.last_scheduled.clear()
            states = {'alpha': {'posts': [{'shortcode': 'ready', 'is_video': True}], 'has_more': True}}
            self.refill.schedule({'served': set()}, names, set(names), states=states)
            self.assertNotIn('alpha', self.refill.pending, 'empty sources retain priority over buffered ones')

    def test_reported_latency_measures_work_not_time_until_collect(self):
        clock = [100.]
        def load(name):
            clock[0] += 20
            return {}, True
        with patch('ig.refill.time.monotonic', side_effect=lambda:clock[0]), \
             patch('ig.refill.REELS.cached', return_value=None), \
             patch('ig.refill.REELS.load', side_effect=load):
            self.refill.schedule({'served':set()}, ['creator'], {'creator'}, states={})
            for future,_ in self.refill.pending.values():
                future.result(timeout=2)
            clock[0] += 100
            self.refill.collect({}, lambda *args:None)
        self.assertEqual(self.refill.load_seconds,16.)
        self.assertFalse(self.refill.pending)

    @staticmethod
    def archive_state(count=12):
        return {'posts': [{'shortcode': str(i), 'is_video': True} for i in range(count)],
                'pending': [], 'has_more': True, 'cursor': 'older', 'page_has_more': True}

    def test_older_pages_share_cadence_with_discovery_and_alternate(self):
        clock = [100.]
        states = {'saved': self.archive_state()}
        pool = {'served': set()}
        with patch('ig.refill.time.monotonic', side_effect=lambda: clock[0]), \
             patch('ig.refill.REELS.cached', return_value=None), \
             patch.object(self.refill.executor, 'submit', return_value=MagicMock()) as submit:
            modes = []
            for moment in (100, 110, 130, 140, 160):
                clock[0] = moment
                self.refill.schedule(pool, ['saved', 'trial'], {'saved'}, states=states)
                if self.refill.pending:
                    modes.append((moment, submit.call_args.args[1:]))
                    self.refill.pending.clear()
                    self.refill.exploration_pending.clear()
            self.assertEqual(modes, [(100, ('trial', False)),
                                     (130, ('saved', 'explore')),
                                     (160, ('trial', False))])

    def test_older_pages_rotate_saved_and_accepted_accounts(self):
        clock = [100.]
        names = ['saved', 'accepted', 'another']
        states = {name: self.archive_state() for name in names}
        pool = {'served': set(), 'accepted': {'accepted'}}
        with patch('ig.refill.time.monotonic', side_effect=lambda: clock[0]), \
             patch.object(self.refill.executor, 'submit', return_value=MagicMock()):
            visited = []
            for _ in range(3):
                self.refill.schedule(pool, names, {'saved', 'another'}, states=states)
                self.assertEqual(len(self.refill.pending), 1)
                visited.extend(self.refill.pending)
                self.refill.pending.clear()
                self.refill.exploration_pending.clear()
                clock[0] += 30
            self.assertEqual(set(visited), set(names))

    def test_archive_work_respects_empty_source_priority_and_worker_limit(self):
        states = {'archive': self.archive_state()}
        with patch('ig.refill.REELS.cached', return_value=None), \
             patch.object(self.refill.executor, 'submit', return_value=MagicMock()) as submit:
            self.refill.schedule({'served': set()}, ['archive', 'cold1', 'cold2'],
                                 {'archive', 'cold1', 'cold2'}, states=states)
            self.assertEqual(set(self.refill.pending), {'cold1', 'cold2'})
            self.assertEqual(self.refill.next_discovery_at, 0)
            self.refill.schedule({'served': set()}, ['archive', 'cold1', 'cold2'],
                                 {'archive', 'cold1', 'cold2'}, states=states)
            self.assertEqual(submit.call_count, 2)

    def test_low_buffer_only_replenishes_without_optional_archive_work(self):
        with patch.object(self.refill.executor, 'submit', return_value=MagicMock()) as submit:
            self.refill.schedule({'served': set()}, ['saved'], {'saved'},
                                 states={'saved': self.archive_state(5)})
            self.assertEqual(submit.call_args.args[1:], ('saved', True))
            self.assertEqual(self.refill.next_discovery_at, 0)

    def test_stale_full_archive_refresh_is_throttled_when_cache_stays_stale(self):
        clock = [100.]
        state = self.archive_state()
        state.update(fetched_at=1, cursor=None, page_has_more=False)
        with patch('ig.refill.time.monotonic', side_effect=lambda: clock[0]), \
             patch.object(self.refill.executor, 'submit', return_value=MagicMock()) as submit:
            self.refill.schedule({'served': set()}, ['saved'], {'saved'}, states={'saved': state})
            self.assertEqual(submit.call_args.args[1:], ('saved', False))
            self.refill.pending.clear()
            clock[0] = 101
            self.refill.schedule({'served': set()}, ['saved'], {'saved'}, states={'saved': state})
            self.assertEqual(submit.call_count, 1)
            clock[0] = 400
            self.refill.schedule({'served': set()}, ['saved'], {'saved'}, states={'saved': state})
            self.assertEqual(submit.call_count, 2)

    def test_archive_retry_waits_without_empty_worker_jobs(self):
        state = self.archive_state(0)
        state['archive_retry_at'] = 1030
        with patch('ig.refill.time.time', return_value=1000), \
             patch('ig.refill.time.monotonic', return_value=100), \
             patch.object(self.refill.executor, 'submit') as submit:
            self.refill.schedule({'served': set()}, ['saved'], {'saved'}, states={'saved': state})
            submit.assert_not_called()
            self.assertEqual(self.refill.retry_at['saved'], 130)
            self.assertTrue(self.refill.waiting(['saved']))

    def test_global_backoff_suppresses_all_worker_jobs(self):
        with patch('ig.refill.BACKOFF.remaining', return_value=30), \
             patch.object(self.refill.executor, 'submit') as submit:
            self.refill.schedule({'served': set()}, ['saved'], {'saved'},
                                 states={'saved': self.archive_state()})
            submit.assert_not_called()
            self.assertEqual(self.refill.next_discovery_at, 0)

    def test_archive_worker_dispatches_explore_and_collect_releases_shared_slot(self):
        with patch('ig.refill.REELS.explore', return_value={}) as explore:
            self.refill.schedule({'served': set()}, ['saved'], {'saved'},
                                 states={'saved': self.archive_state()})
            for future, _ in self.refill.pending.values():
                future.result(timeout=2)
            self.assertEqual(self.refill.exploration_pending, {'saved'})
            self.refill.collect({}, lambda *args: None)
            self.assertFalse(self.refill.exploration_pending)
            explore.assert_called_once_with('saved')

    def test_archive_can_replace_buffered_recent_paging_without_double_submission(self):
        state = self.archive_state(8)
        state['pending'] = [{'shortcode': 'recent', 'reel_age_group': 'recent'}]
        with patch.object(self.refill.executor, 'submit', return_value=MagicMock()) as submit:
            self.refill.schedule({'served': set()}, ['saved'], {'saved'}, states={'saved': state})
            self.assertEqual(submit.call_count, 1)
            self.assertEqual(submit.call_args.args[1:], ('saved', 'explore'))

    def test_accepted_creator_refill_still_consumes_discovery_budget(self):
        state = self.archive_state(1)
        state['pending'] = [{'shortcode': 'pending'}]
        with patch.object(self.refill.executor, 'submit', return_value=MagicMock()) as submit:
            self.refill.next_discovery_at = float('inf')
            self.refill.schedule({'served': set(), 'accepted': {'accepted'}}, ['accepted'], set(),
                                 states={'accepted': state})
            submit.assert_not_called()

    def test_global_pause_keeps_empty_feed_waiting_for_retry(self):
        with patch('ig.refill.BACKOFF.remaining', return_value=30), \
             patch('ig.refill.time.monotonic', return_value=100):
            self.refill.schedule({'served': set()}, ['cold'], {'cold'}, states={})
            self.assertTrue(self.refill.waiting(['cold']))
            self.assertEqual(self.refill.retry_at['cold'], 130)

    def test_last_archive_page_pending_details_continue_in_background(self):
        state = self.archive_state()
        state.update(page_has_more=False, pending=[{'shortcode': 'last', 'reel_age_group': 'older'}])
        with patch.object(self.refill.executor, 'submit', return_value=MagicMock()) as submit:
            self.refill.schedule({'served': set()}, ['saved'], {'saved'}, states={'saved': state})
            self.assertEqual(submit.call_args.args[1:], ('saved', 'explore'))

    def test_transient_latest_refresh_failure_keeps_usable_cached_creator(self):
        state = self.archive_state()
        state['fetched_at'] = 1
        errors = MagicMock()
        with patch('ig.refill.REELS.load', side_effect=OSError('offline')):
            self.refill.schedule({'served': set()}, ['saved'], {'saved'}, states={'saved': state})
            for future, _ in self.refill.pending.values():
                with self.assertRaises(OSError):
                    future.result(timeout=2)
            self.refill.collect({}, errors)
        errors.assert_not_called()
        self.assertFalse(self.refill.retained_refresh)
        self.assertIn('saved', self.refill.retry_at)
        self.assertEqual(len(state['posts']), 12)
