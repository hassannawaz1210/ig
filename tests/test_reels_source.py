import json
import random
from unittest.mock import patch, MagicMock
from urllib.parse import parse_qs

from ig import instagram
from ig.reels import ReelStore
from test_profile_cache import TempDirs, profile_html, response


def listing(codes, cursor=None):
    return {'reels': [{'shortcode':code, 'play_count':123, 'image':'thumb'} for code in codes],
            'cursor':cursor, 'has_more':bool(cursor)}


def detail(code):
    return {'shortcode':code, 'owner':'creator', 'caption':'gym memes', 'video':'https://video',
            'video_dash_manifest':'manifest', 'audio_id':'song'}


class ReelSourceTests(TempDirs):
    def test_reels_query_reuses_session_and_cursor_without_grid_pagination(self):
        session = MagicMock()
        session.cookies.get.return_value = ''
        payload = {'data':{'xig_user_by_username':{'user':{'polaris_clips_connection':{
            'edges':[{'node':{'code':'clip001', 'play_count':42}}],
            'page_info':{'end_cursor':'next', 'has_next_page':True}}}}}}
        context = {}
        with patch.object(instagram, 'fetch_profile_page', return_value=(session, profile_html())) as bootstrap, \
             patch.object(instagram, 'request', return_value=response(body=payload)) as request:
            first = instagram.reels_tab('creator', context=context)
            instagram.reels_tab('creator', after=first['cursor'], context=context)
        bootstrap.assert_called_once()
        body = parse_qs(request.call_args.kwargs['data'])
        self.assertEqual(body['doc_id'], [instagram.REELS_DOC_ID])
        self.assertEqual(json.loads(body['variables'][0])['after'], 'next')
        self.assertEqual(first['reels'][0]['play_count'],42)

    def test_bounded_details_independent_cache_and_resume_pending_after_restart(self):
        store = ReelStore()
        with patch.object(instagram, 'reels_tab', side_effect=[
                listing(['one','two','three','four'], 'next'), listing(['four','five'])]) as tabs, \
             patch.object(instagram, 'media_info', side_effect=detail) as media, \
             patch.object(instagram.PROFILES, 'load', side_effect=AssertionError('No grid fetching')):
            state = store.get('creator')
            self.assertEqual(len(state['posts']),3)
            self.assertEqual(media.call_count,3)
            self.assertEqual(state['posts'][0]['caption'],'gym memes')
            self.assertEqual(state['posts'][0]['play_count'],123)
            self.assertEqual(store.media(state['posts'][0]['shortcode'])['video_dash_manifest'],'manifest')
            self.assertEqual(media.call_count,3, 'playback reuses fetched details')
            resumed = ReelStore()
            resumed.more('creator')
            self.assertEqual(tabs.call_count,1, 'pending listings survive restart')
            resumed.more('creator')
            self.assertCountEqual([p['shortcode'] for p in resumed.get('creator')['posts']],
                                  ['one','two','three','four','five'])
            self.assertFalse(resumed.get('creator')['has_more'])
            self.assertEqual(tabs.call_args.kwargs['after'],'next')
        self.assertFalse(instagram.ProfileCache().path.exists())
        raw = store.cache.load('creator',store.cache.FRESH_FOR)
        self.assertNotIn('session',raw)
        self.assertNotIn('video', raw['posts'][0])

    def test_fresh_listing_varies_first_details_without_extra_requests_or_lost_clips(self):
        codes = ['clip' + str(i) for i in range(12)]
        first_batches = []
        for seed in (1, 2, 3):
            store = ReelStore()
            rng = random.Random(seed)
            with patch('ig.reels.random.shuffle', side_effect=rng.shuffle), \
                 patch.object(instagram, 'reels_tab', return_value=listing(codes)) as tabs, \
                 patch.object(instagram, 'media_info', side_effect=detail) as media:
                username = 'creator' + str(seed)
                state = store.fetch(username)
                first_batches.append(tuple(p['shortcode'] for p in state['posts']))
                self.assertEqual(media.call_count, 3)
                self.assertEqual(len(state['pending']), 9)
                pending = [p['shortcode'] for p in state['pending']]
                resumed = ReelStore()
                cached = resumed.get(username)
                self.assertEqual([p['shortcode'] for p in cached['pending']], pending)
                while cached['has_more']:
                    resumed.more(username)
                self.assertCountEqual([p['shortcode'] for p in cached['posts']], codes)
                self.assertEqual(media.call_count, len(codes))
                tabs.assert_called_once()
        self.assertEqual(len(set(first_batches)), 3)
        self.assertTrue(any(set(batch) != set(codes[:3]) for batch in first_batches))

    def test_rate_limit_keeps_pending_work_and_does_not_fall_back_to_grid(self):
        store = ReelStore()
        with patch.object(instagram, 'reels_tab', return_value=listing(['one','two'])), \
             patch.object(instagram, 'media_info', side_effect=[detail('one'),instagram.RateLimited('wait')]):
            state, _ = store.load('creator')
        self.assertEqual([p['shortcode'] for p in state['posts']],['one'])
        self.assertEqual([p['shortcode'] for p in state['pending']],['two'])
        with patch.object(instagram, 'media_info', side_effect=detail):
            store.more('creator')
        self.assertFalse(state['has_more'])
        self.assertEqual(len(state['posts']),2)

    def test_deleted_post_does_not_kill_creator_and_repeated_cursor_stops(self):
        store = ReelStore()
        with patch.object(instagram, 'reels_tab', side_effect=[
                listing(['gone','good'],'next'), listing(['good'],'next')]), \
             patch.object(instagram, 'media_info', side_effect=[instagram.ProfileUnavailable('gone'),detail('good')]):
            state = store.get('creator')
            store.more('creator')
        self.assertEqual([p['shortcode'] for p in state['posts']],['good'])
        self.assertFalse(state['has_more'])

    def test_creators_share_setup_and_do_not_inherit_bootstrap_bio(self):
        store = ReelStore()
        session = MagicMock()
        session.cookies.get.return_value = ''
        payload = {'data':{'xig_user_by_username':{'polaris_clips_connection':{
            'edges':[], 'page_info':{'has_next_page':False}}}}}
        with patch.object(instagram, 'fetch_profile_page', return_value=(session, profile_html())) as bootstrap, \
             patch.object(instagram, 'request', return_value=response(body=payload)):
            first = store.get('first')
            second = store.get('second')
        bootstrap.assert_called_once_with('first')
        self.assertEqual(first['biography'], '')
        self.assertEqual(second['biography'], '')
        self.assertNotIn('session', first)
        self.assertNotIn('session', second)
