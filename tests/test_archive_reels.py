import json
import time
from unittest.mock import patch

from ig import instagram
from ig.reels import ReelStore
from test_profile_cache import TempDirs
from test_reels_source import listing, detail


def page(prefix, cursor):
    return listing([prefix + str(i) for i in range(12)], cursor)


class ArchiveReelsTests(TempDirs):
    def test_explore_advances_before_current_details_finish_and_keeps_cursor_on_refresh(self):
        store = ReelStore()
        with patch.object(instagram, 'reels_tab', side_effect=[page('new', 'page2'), page('old', 'page3'),
                listing(['latest', 'old0'], 'new-page2')]) as tabs, \
             patch.object(instagram, 'media_info', side_effect=detail) as media:
            state = store.get('creator')
            self.assertEqual((tabs.call_count, media.call_count), (1, 3))
            self.assertEqual(len(state['pending']), 9)
            older = store.explore('creator')['posts']
            self.assertEqual((tabs.call_count, media.call_count), (2, 6))
            self.assertEqual(tabs.call_args.kwargs['after'], 'page2')
            self.assertEqual(len(older), 3)
            self.assertTrue(all(p['reel_age_group'] == 'older' for p in older))
            self.assertEqual(sum(p['reel_age_group'] == 'recent' for p in state['pending']), 9)
            store.fetch('creator')
            self.assertIsNone(tabs.call_args.kwargs['after'])
            self.assertEqual(state['cursor'], 'page3')
            self.assertEqual(state['latest_cursor'], 'new-page2')
            all_items = state['posts'] + state['pending']
            self.assertEqual(len(all_items), 25)
            self.assertTrue(all(p['reel_age_group'] == ('recent' if p['shortcode'] in ['latest', 'old0'] else 'older')
                                for p in all_items))
        resumed = ReelStore()
        with patch.object(instagram, 'reels_tab') as network:
            cached = resumed.cached('creator')
        network.assert_not_called()
        self.assertEqual(cached['cursor'], 'page3')
        self.assertEqual(len(cached['posts']) + len(cached['pending']), 25)

    def test_retained_startup_is_immediate_but_load_refreshes_stale_latest(self):
        store = ReelStore()
        with patch.object(instagram, 'reels_tab', side_effect=[page('new', 'page2'), page('old', 'page3')]), \
             patch.object(instagram, 'media_info', side_effect=detail):
            store.get('creator')
            store.explore('creator')
        state = store.memory['creator']
        state['fetched_at'] = time.time() - 7200
        store.cache.save('creator', state)
        resumed = ReelStore()
        with patch.object(instagram, 'reels_tab', return_value=page('today', 'today-page2')) as tabs, \
             patch.object(instagram, 'media_info', side_effect=detail):
            cached = resumed.cached('creator')
            self.assertTrue(resumed.is_stale(cached))
            self.assertIn('creator', resumed.cache.fresh_usernames())
            tabs.assert_not_called()
            refreshed, network = resumed.load('creator')
            self.assertTrue(network)
            tabs.assert_called_once()
            self.assertIsNone(tabs.call_args.kwargs['after'])
            self.assertEqual(refreshed['cursor'], 'page3')
            self.assertFalse(resumed.is_stale(refreshed))

    def test_many_pages_bound_metadata_and_do_not_persist_media(self):
        store = ReelStore()
        pages = [page('page' + str(i) + '_', 'cursor' + str(i + 1)) for i in range(48)]
        with patch.object(instagram, 'reels_tab', side_effect=pages) as tabs, \
             patch.object(instagram, 'media_info', side_effect=detail) as media:
            state = store.get('creator')
            for _ in range(47):
                prior_pages, prior_details = tabs.call_count, media.call_count
                store.explore('creator')
                self.assertEqual(tabs.call_count - prior_pages, 1)
                self.assertLessEqual(media.call_count - prior_details, 3)
                self.assertLessEqual(len(state['posts']), store.MAX_POSTS)
                self.assertLessEqual(len(state['pending']), store.MAX_PENDING)
                self.assertLessEqual(len(state['seen_codes']), store.MAX_KNOWN_CODES)
                codes = [p['shortcode'] for p in state['posts'] + state['pending']]
                self.assertEqual(len(codes), len(set(codes)))
        raw = store.cache.load('creator', store.cache.KEEP_FOR)
        self.assertEqual(raw['cursor'], 'cursor48')
        for item in raw['posts'] + raw['pending']:
            self.assertNotIn('video', item)
            self.assertNotIn('video_dash_manifest', item)
        self.assertNotIn('session', raw)

    def test_invalid_archive_cursor_has_bounded_recovery_without_losing_pool(self):
        store = ReelStore()
        with patch.object(instagram, 'reels_tab', return_value=page('new', 'stale')), \
             patch.object(instagram, 'media_info', side_effect=detail):
            state = store.get('creator')
        clock = [time.time()]
        with patch('ig.reels.time.time', side_effect=lambda: clock[0]), \
             patch.object(instagram, 'reels_tab', side_effect=instagram.UnexpectedResponse('bad cursor')) as tabs:
            store.explore('creator')
            self.assertEqual(tabs.call_count, 1)
            self.assertFalse(store.can_explore(state))
            self.assertEqual(state['cursor'], 'stale')
            store.explore('creator')
            self.assertEqual(tabs.call_count, 1, 'no retry during cooldown')
            clock[0] += 31
            store.explore('creator')
            self.assertEqual(tabs.call_count, 2)
            self.assertTrue(state['archive_reset_pending'])
            self.assertEqual(state['cursor'], 'stale')
            self.assertFalse(store.can_explore(state))
            clock[0] += store.ARCHIVE_RETRY_SECONDS + 1
            with patch.object(instagram, 'reels_tab', return_value=page('new', 'recovered')) as recovered:
                store.explore('creator')
            recovered.assert_called_once()
            self.assertIsNone(recovered.call_args.kwargs['after'])
            self.assertEqual(state['cursor'], 'recovered')
            self.assertFalse(state['archive_reset_pending'])
            self.assertEqual(len(state['posts']) + len(state['pending']), 12)

    def test_backoff_and_network_failures_never_reset_archive_progress(self):
        store = ReelStore()
        with patch.object(instagram, 'reels_tab', return_value=page('new', 'keep')), \
             patch.object(instagram, 'media_info', side_effect=detail):
            state = store.get('creator')
        for error in (instagram.RateLimited('wait'), instagram.NetworkError('offline')):
            with patch.object(instagram, 'reels_tab', side_effect=error) as tabs:
                with self.assertRaises(type(error)):
                    store.explore('creator')
            tabs.assert_called_once()
            self.assertEqual(state['cursor'], 'keep')
            self.assertFalse(state.get('archive_reset_pending'))
            self.assertEqual(state.get('archive_failures', 0), 0)

    def test_repeated_cursor_pauses_and_duplicate_listing_keeps_latest_classification(self):
        store = ReelStore()
        with patch.object(instagram, 'reels_tab', side_effect=[listing(['new'], 'repeat'),
                listing(['new', 'old'], 'repeat')]) as tabs, \
             patch.object(instagram, 'media_info', side_effect=detail):
            state = store.get('creator')
            store.explore('creator')
        self.assertEqual(tabs.call_count, 2)
        self.assertFalse(store.can_explore(state))
        self.assertTrue(state['archive_reset_pending'])
        self.assertGreater(state['archive_retry_at'], time.time())
        self.assertEqual({p['shortcode']: p['reel_age_group'] for p in state['posts']},
                         {'new': 'recent', 'old': 'older'})

    def test_demand_details_can_choose_either_age_group(self):
        for pick, expected in ((0, 'older'), (1, 'recent')):
            state = {'posts': [], 'pending': [
                {'shortcode': group + str(i), 'reel_age_group': group}
                for group in ('recent', 'older') for i in range(4)], 'page_has_more': False}
            with patch('ig.reels.random.random', return_value=pick), \
                 patch.object(instagram, 'media_info', side_effect=detail) as media:
                fresh = ReelStore()._details('creator', state)
            self.assertEqual(media.call_count, 3)
            self.assertEqual([p['reel_age_group'] for p in fresh], [expected] * 3)
