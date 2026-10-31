import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from curl_cffi import requests as cr

from ig import config, feeds, instagram
from ig.instagram import (Backoff, ProfileCache, ProfileStore, RateLimited, ProfileUnavailable,
                          UnexpectedResponse, NetworkError)


def profile_html(username='nasa', posts=1, private=False):
    user = {'id': '42', 'username': username, 'biography': 'space', 'is_private': private}
    payload = {'xig_user_by_username': user}
    if not private:
        payload['polaris_ordered_timeline_connection'] = {
            'edges': [{'node': {'code': f'code{i:04d}', 'media_type': 2, 'user': {'username': username}}}
                      for i in range(posts)],
            'page_info': {'end_cursor': 'next', 'has_next_page': True}}
    return ('<script type="application/json" data-sjs>' + json.dumps(payload) + '</script>'
            '"LSD",[],{"token":"lsdtoken"}')


def response(status=200, text='', url='https://www.instagram.com/nasa/', body=None):
    r = MagicMock()
    r.status_code, r.text, r.url = status, text, url
    if body is None:
        r.json.side_effect = ValueError('not json')
    else:
        r.json.return_value = body
    return r


class TempDirs(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name)
        for target, value in [('ig.config.CONFIG_DIR', path / 'config'), ('ig.config.CACHE', path / 'cache')]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        backoff = patch('ig.instagram.BACKOFF', Backoff())
        backoff.start()
        self.addCleanup(backoff.stop)
        self.store = ProfileStore(ProfileCache())


class ProfileCacheTests(TempDirs):
    def test_round_trip_keeps_metadata_but_not_sessions(self):
        state = {'session': object(), 'lsd': 'secret', 'csrf': 'secret', 'user_id': '1',
                 'username': 'nasa', 'biography': 'bio', 'posts': [{'shortcode': 'a'}],
                 'cursor': 'c', 'has_more': True, 'fetched_at': time.time()}
        self.store.cache.save('nasa', state)
        loaded = self.store.cache.load('nasa', 60)
        self.assertEqual(loaded['posts'], [{'shortcode': 'a'}])
        self.assertNotIn('session', loaded)
        self.assertNotIn('lsd', loaded)
        self.assertIn('lock', loaded)
        self.assertNotIn('secret', self.store.cache.path.read_bytes().decode('latin-1'))

    def test_old_entries_are_not_fresh(self):
        self.store.cache.save('nasa', {'posts': [], 'fetched_at': time.time() - 2 * ProfileCache.FRESH_FOR})
        self.assertIsNone(self.store.cache.load('nasa', ProfileCache.FRESH_FOR))
        self.assertEqual(self.store.cache.fresh_usernames(), set())
        self.assertIsNotNone(self.store.cache.load('nasa', ProfileCache.FALLBACK_FOR))

    def test_missing_cache_file_is_not_created_by_reads(self):
        self.assertIsNone(self.store.cache.load('nasa', 60))
        self.assertFalse(self.store.cache.path.exists())

    def test_corrupt_cache_entry_is_refetched_not_fatal(self):
        self.store.cache.save('nasa', {'posts': [], 'fetched_at': time.time()})
        with self.store.cache._database() as connection:
            connection.execute("UPDATE profiles SET data = 'not json'")
        with self.assertLogs('ig.instagram', 'ERROR'):
            self.assertIsNone(self.store.cache.load('nasa', 60))

    def test_restart_uses_disk_cache_without_a_request(self):
        with patch('ig.instagram.fetch_profile_page', return_value=(MagicMock(), profile_html())) as fetch:
            self.store.load('nasa')
            restarted = ProfileStore(ProfileCache())
            state, used_network = restarted.load('nasa')
        self.assertEqual(fetch.call_count, 1)
        self.assertFalse(used_network)
        self.assertEqual(state['biography'], 'space')

    def test_stale_copy_is_used_while_rate_limited(self):
        self.store.cache.save('nasa', {'posts': [{'shortcode': 'old'}], 'username': 'nasa',
                                       'fetched_at': time.time() - 2 * ProfileCache.FRESH_FOR})
        with patch('ig.instagram.fetch_profile_page', side_effect=RateLimited('wait')), \
                self.assertLogs('ig.instagram', 'WARNING'):
            state, _ = self.store.load('nasa')
        self.assertEqual(state['posts'], [{'shortcode': 'old'}])

    def test_rate_limit_without_any_copy_is_raised(self):
        with patch('ig.instagram.fetch_profile_page', side_effect=RateLimited('wait')):
            with self.assertRaises(RateLimited):
                self.store.load('nasa')

    def test_cached_profile_pages_after_restoring_a_session(self):
        self.store.cache.save('nasa', {'posts': [], 'username': 'nasa', 'user_id': '42', 'cursor': 'c',
                                       'has_more': True, 'fetched_at': time.time()})
        session = MagicMock()
        session.cookies.get.return_value = 'csrf'
        body = {'data': {'node': {'polaris_ordered_timeline_connection': {
            'edges': [{'node': {'code': 'more0001', 'user': {'username': 'nasa'}}}],
            'page_info': {'has_next_page': False}}}}}
        session.request.return_value = response(body=body)
        with patch('ig.instagram.fetch_profile_page', return_value=(session, profile_html())) as fetch, \
                patch('ig.instagram.time.sleep'):
            result = self.store.more('nasa')
        fetch.assert_called_once()
        self.assertEqual([p['shortcode'] for p in result['posts']], ['more0001'])
        self.assertEqual(len(self.store.cache.load('nasa', 60)['posts']), 1)

    def test_non_json_pagination_is_a_rate_limit(self):
        session = MagicMock()
        session.request.return_value = response(text='<html>login</html>')
        self.store.memory['nasa'] = {'session': session, 'lsd': 'x', 'csrf': '', 'user_id': '1',
                                     'cursor': 'c', 'has_more': True, 'posts': []}
        with patch('ig.instagram.time.sleep'), self.assertLogs('ig.instagram', 'WARNING'):
            with self.assertRaises(RateLimited):
                self.store.more('nasa')

    def test_empty_pagination_points_at_the_query_id(self):
        session = MagicMock()
        session.request.return_value = response(body={'data': {}})
        self.store.memory['nasa'] = {'session': session, 'lsd': 'x', 'csrf': '', 'user_id': '1',
                                     'cursor': 'c', 'has_more': True, 'posts': []}
        with patch('ig.instagram.time.sleep'), self.assertLogs('ig.instagram', 'WARNING'):
            with self.assertRaisesRegex(UnexpectedResponse, 'query ID'):
                self.store.more('nasa')


class RequestTests(TempDirs):
    def test_429_starts_a_shared_backoff(self):
        session = MagicMock()
        session.request.return_value = response(status=429)
        with self.assertLogs('ig.instagram', 'WARNING'):
            with self.assertRaises(RateLimited):
                instagram.request(session, 'GET', 'https://www.instagram.com/x/')
        with self.assertRaisesRegex(RateLimited, 'retrying in'):
            instagram.request(session, 'GET', 'https://www.instagram.com/y/')
        self.assertEqual(session.request.call_count, 1, 'no request is sent while paused')

    def test_backoff_doubles_and_resets(self):
        backoff = Backoff(first=10, longest=25)
        with self.assertLogs('ig.instagram', 'WARNING'):
            self.assertEqual([backoff.hit(), backoff.hit(), backoff.hit()], [10, 20, 25])
        backoff.reset()
        self.assertEqual(backoff.remaining(), 0)

    def test_login_redirect_is_a_rate_limit(self):
        session = MagicMock()
        session.request.return_value = response(url='https://www.instagram.com/accounts/login/?next=x')
        with self.assertLogs('ig.instagram', 'WARNING'), self.assertRaisesRegex(RateLimited, 'log in'):
            instagram.request(session, 'GET', 'https://www.instagram.com/x/')

    def test_connection_failure_is_a_network_error(self):
        session = MagicMock()
        session.request.side_effect = cr.exceptions.ConnectionError('offline')
        with self.assertLogs('ig.instagram', 'WARNING'), self.assertRaises(NetworkError):
            instagram.request(session, 'GET', 'https://www.instagram.com/x/')

    def test_missing_and_private_profiles_are_unavailable(self):
        with patch('ig.instagram._new_session') as new:
            new.return_value.request.return_value = response(status=404)
            with self.assertRaises(ProfileUnavailable):
                instagram.fetch_profile_page('gone')
        with self.assertRaisesRegex(ProfileUnavailable, 'private'):
            instagram.parse_profile(profile_html(private=True), 'secret')

    def test_page_without_posts_is_unexpected(self):
        with self.assertRaises(UnexpectedResponse):
            instagram.parse_profile('<html></html>', 'nasa')


class BatchFailureTests(TempDirs):
    def setUp(self):
        super().setUp()
        config.save_feeds({'memes': ['saved', 'broken']})

    def run_batch(self, pool, error):
        states = {'saved': {'posts': [{'shortcode': 'clip01', 'owner': 'saved', 'is_video': True}],
                            'has_more': False}}

        def load(username):
            raise error
        with patch('ig.feeds.STATE', states), patch.object(feeds.PROFILES, 'load', side_effect=load), \
                patch('ig.feeds.random.random', return_value=1):
            return feeds._pool_more(pool)

    def test_rate_limited_accounts_are_retried_later(self):
        pool = feeds.make_pool(['saved', 'broken'])
        with self.assertLogs('ig.feeds', 'WARNING'):
            result = self.run_batch(pool, RateLimited('wait'))
        self.assertEqual([p['owner'] for p in result['posts']], ['saved'])
        self.assertNotIn('broken', pool['dead'])

    def test_unavailable_accounts_are_dropped(self):
        pool = feeds.make_pool(['saved', 'broken'])
        with self.assertLogs('ig.feeds', 'INFO'):
            self.run_batch(pool, ProfileUnavailable('gone'))
        self.assertIn('broken', pool['dead'])

    def test_unclear_failures_drop_an_account_after_three_tries(self):
        pool = feeds.make_pool(['saved', 'broken'])
        for attempt in range(feeds.MAX_FAILURES):
            self.assertNotIn('broken', pool['dead'])
            with self.assertLogs('ig.feeds', 'INFO'):
                self.run_batch(pool, UnexpectedResponse('odd'))
        self.assertIn('broken', pool['dead'])

    def test_delivered_reels_do_not_change_profile_posts(self):
        post = {'shortcode': 'clip01', 'owner': 'saved', 'is_video': True}
        states = {'saved': {'posts': [post], 'has_more': False}}
        config.save_feeds({'memes': ['saved']})
        with patch('ig.feeds.STATE', states):
            result = feeds._pool_more(feeds.make_pool(['saved']))
        self.assertIn('reason', result['posts'][0])
        self.assertNotIn('reason', post)


if __name__ == '__main__':
    unittest.main()


class RequestBudgetTests(TempDirs):
    """After a restart nothing is in memory; off-topic leads must not starve saved accounts."""

    def test_trials_cannot_use_up_requests_meant_for_saved_accounts(self):
        saved = ['one', 'two', 'three']
        leads = [f'stranger{i}' for i in range(30)]
        config.save_feeds({'memes': saved})
        loaded = []

        def load(username):
            loaded.append(username)
            return ({'posts': [{'shortcode': f'{username}0001', 'owner': username, 'is_video': True,
                                'caption': 'university campus tour'}], 'has_more': False,
                     'biography': 'Official university account'}, True)
        pool = feeds.make_pool(saved)
        feeds.discover_into(pool, leads)
        # 0.1 makes every pick prefer a new creator, the worst case for saved accounts.
        with patch('ig.feeds.STATE', {}), patch.object(feeds.PROFILES, 'load', side_effect=load), \
                patch('ig.feeds.random.random', return_value=0.1):
            result = feeds._pool_more(pool)
        strangers = [name for name in loaded if name.startswith('stranger')]
        self.assertLessEqual(len(strangers), 1, 'at most one off-topic trial is loaded')
        self.assertLessEqual(len(loaded), 3)
        self.assertGreaterEqual(len({p['owner'] for p in result['posts']} & set(saved)), 2,
                                'the remaining requests load saved accounts')
