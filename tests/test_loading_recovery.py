"""Anonymous session, backoff and playback recovery regressions."""
import time
from unittest.mock import MagicMock, patch
from ig import config, dash, feeds, instagram, server
from ig.reels import ReelStore
from test_profile_cache import TempDirs, response
from test_comment_leads import POST_HTML, comments_payload
from test_reels_source import listing, detail
from test_video_quality import mpd


class LoadingRecoveryTests(TempDirs):
    def setUp(self):
        super().setUp()
        instagram._COMMENT_CONTEXTS.clear()
        instagram._COMMENT_PAGES.clear()
        self.addCleanup(instagram._COMMENT_CONTEXTS.clear)
        self.addCleanup(instagram._COMMENT_PAGES.clear)

    def test_comments_share_session_and_cache_pages(self):
        session = MagicMock()
        session.cookies.get.return_value = 'csrf-from-page'
        session.request.side_effect = [response(text=POST_HTML),
            response(body=comments_payload([('fan', 'nice', 1)], cursor='next')),
            response(body=comments_payload([], more=False))]
        with patch.object(instagram, '_new_session', return_value=session) as new:
            instagram._post_media('example')
            first = instagram.post_comments('example')
            first['comments'].clear()
            self.assertEqual(len(instagram.post_comments('example')['comments']), 1)
            instagram.post_comments('example', after='next')
        new.assert_called_once()
        self.assertEqual([c.args[0] for c in session.request.call_args_list], ['GET', 'POST', 'POST'])
        self.assertEqual(session.request.call_args.kwargs['headers']['x-csrftoken'], 'csrf-from-page')

    def test_cold_comments_use_one_session(self):
        session = MagicMock()
        session.request.side_effect = [response(text=POST_HTML), response(body=comments_payload([]))]
        with patch.object(instagram, '_new_session', return_value=session) as new:
            instagram.post_comments('cold')
        new.assert_called_once()
        self.assertEqual(session.request.call_count, 2)

    def test_body_limits_grow_until_valid_response(self):
        session = MagicMock()
        session.request.return_value = response(body={'require_login': True})
        context = {'session': session, 'lsd': 'token'}
        clock = 1000
        with patch.object(instagram.time, 'time', side_effect=lambda: clock):
            for delay in [30, 60, 120, 240, 480, 600]:
                with self.assertRaises(instagram.RateLimited):
                    instagram.reels_tab('creator', context=context)
                self.assertEqual(instagram.BACKOFF.remaining(), delay)
                clock += delay + 1
            session.request.return_value = response(body={'data': {'xig_user_by_username': {
                'polaris_clips_connection': {'edges': [], 'page_info': {}}}}})
            instagram.reels_tab('creator', context=context)
            self.assertEqual(instagram.BACKOFF.strikes, 0)

    def test_failed_and_expired_reels_session_refresh(self):
        store = ReelStore()
        store.query_context.update(session=object(), lsd='expired')
        state = {'posts': [], 'cursor': 'keep-cursor'}
        with patch.object(instagram, 'reels_tab', side_effect=instagram.UnexpectedResponse('token')):
            with self.assertRaises(instagram.UnexpectedResponse):
                store._page('creator', state)
        self.assertEqual(store.query_context, {})
        def fresh(username, *, after, context):
            self.assertNotIn('session', context)
            self.assertEqual(after, 'keep-cursor')
            context.update(session=object(), lsd='fresh')
            return listing([])
        store.query_context.update(session=object(), created_at=time.monotonic()-901)
        with patch.object(instagram, 'reels_tab', side_effect=fresh):
            store._page('creator', state)
        self.assertEqual(store.query_context['lsd'], 'fresh')

    def test_bad_clip_retries_bounded_without_blocking_good_clip(self):
        store = ReelStore()
        attempts = []
        def fetch(code):
            attempts.append(code)
            if code == 'bad':
                raise instagram.UnexpectedResponse('gated')
            return detail(code)
        with patch.object(instagram, 'reels_tab', return_value=listing(['bad', 'good'])), \
             patch.object(instagram, 'media_info', side_effect=fetch):
            state = store.get('creator')
            self.assertEqual([p['shortcode'] for p in state['posts']], ['good'])
            store.more('creator')
            store.more('creator')
        self.assertEqual(attempts.count('bad'), 3)
        self.assertFalse(state['has_more'])
        self.assertEqual(state['pending'], [])

    def test_existing_backoff_does_not_count_against_clip(self):
        state = {'posts': [], 'pending': [{'shortcode': 'bad'}], 'page_has_more': False}
        instagram.BACKOFF.hit()
        with patch.object(instagram, 'media_info') as media:
            with self.assertRaises(instagram.RateLimited):
                ReelStore()._details('creator', state)
        media.assert_not_called()
        self.assertNotIn('failures', state['pending'][0])

    def test_cold_video_fallback_survives_assembly_failure(self):
        for error in [None, RuntimeError('ffmpeg missing')]:
            handler = MagicMock()
            with patch.object(dash, 'cached', return_value=None), \
                 patch.object(dash, 'recall', return_value=None), \
                 patch.object(dash, 'assemble', return_value=None, side_effect=error), \
                 patch.object(server, 'media_info', return_value={'video': 'https://video'}):
                server.Handler._video(handler, 'example')
            handler.send_response.assert_called_once_with(302)
            handler.send_header.assert_called_with('Location', 'https://video')
            handler._send.assert_not_called()

    def test_stream_exception_returns_response(self):
        handler = MagicMock()
        with patch.object(dash, 'cached', side_effect=OSError('disk failed')):
            server.Handler._video(handler, 'example')
        self.assertEqual(handler._send.call_args.args[0], 502)

    def test_missing_ffmpeg_returns_none_without_download(self):
        with patch.object(dash, 'CACHE_DIR', config.CACHE/'videos'), \
             patch('imageio_ffmpeg.get_ffmpeg_exe', side_effect=RuntimeError('missing')), \
             patch.object(dash, '_download_track') as download:
            self.assertIsNone(dash.assemble('example', mpd([(1080, 1920)])))
        download.assert_not_called()

    def test_creator_waits_for_busy_workers(self):
        config.save_feeds({'custom': ['creator']})
        with patch.object(feeds, 'STATE', {}), patch.object(feeds, 'POOLS', {}), \
             patch.object(feeds.REELS, 'cached', return_value=None), \
             patch.object(feeds.REFILL, 'collect'), patch.object(feeds.REFILL, 'schedule'), \
             patch.object(feeds.REFILL, 'waiting', return_value=False):
            result = feeds.creator_more('creator', client='client-test')
        self.assertEqual(result['posts'], [])
        self.assertTrue(result['has_more'])

    def test_settings_read_does_not_reserve_writer(self):
        config.save_feeds({'test': ['creator']})
        with config.database(readonly=True):
            with config.database() as writer:
                self.assertTrue(writer.in_transaction)
                writer.rollback()  # Only test reserving the writer, not its commit.

    def test_snapshot_selection_does_not_reopen_databases(self):
        config.save_feeds({'test': ['creator']})
        snapshot = feeds.account_snapshot()
        with patch.object(config, 'database', side_effect=AssertionError('repeated settings read')), \
             patch.object(feeds.recommendations, 'database', side_effect=AssertionError('repeated recommendation read')):
            pool = feeds.make_pool([])
            self.assertEqual(feeds.available_accounts(pool, set(), snapshot), ['creator'])
            self.assertEqual(feeds.available_accounts(pool, {'creator'}, snapshot), [])
