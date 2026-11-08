"""More reels on this sound: Instagram's audio page, without network access."""
import io
import json
import unittest
from unittest.mock import MagicMock, patch

from ig import instagram, server, views
from ig.server import Handler

AUDIO = '561481164338977'


def music_reply(items, more=False):
    response = MagicMock(status_code=200)
    response.text = 'for (;;);' + json.dumps({'payload': {
        'items': [{'media': item} for item in items] + ['not a dict'],
        'paging_info': {'more_available': more, 'max_id': 'cursor-2' if more else None},
        'formatted_media_count': '41K reels'}})
    return response


def reel(code, owner='someone'):
    return {'code': code, 'media_type': 2, 'product_type': 'clips', 'user': {'username': owner},
            'display_uri': f'https://cdn.test/{code}.jpg', 'caption': {'text': 'dance'}}


class AudioReelsTests(unittest.TestCase):
    def setUp(self):
        instagram._AUDIO_CONTEXTS.clear()
        page = MagicMock(status_code=200, text='"LSD",[],{"token":"lsd-token"}<title>Wiz Khalifa | We Dem Boyz | Instagram</title>')
        self.session = MagicMock()
        self.session.cookies.get.return_value = 'csrf'
        self.patches = [patch.object(instagram, '_new_session', return_value=self.session)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.page = page

    def test_first_page_title_count_and_cursor(self):
        with patch.object(instagram, 'request', side_effect=[self.page, music_reply([reel('AAAAAAAAAAA')], more=True)]) as sent:
            data = instagram.audio_reels(AUDIO)
        self.assertEqual(data['title'], 'Wiz Khalifa | We Dem Boyz')
        self.assertEqual(data['total'], '41K reels')
        self.assertEqual([r['shortcode'] for r in data['reels']], ['AAAAAAAAAAA'])
        self.assertEqual((data['cursor'], data['has_more']), ('cursor-2', True))
        self.assertIn('lsd=lsd-token', sent.call_args.kwargs['data'])

    def test_the_page_is_loaded_once_per_sound(self):
        with patch.object(instagram, 'request', side_effect=[self.page, music_reply([]), music_reply([])]) as sent:
            instagram.audio_reels(AUDIO)
            instagram.audio_reels(AUDIO, after='cursor-2')
        self.assertEqual(sent.call_count, 3, 'one page load, two reel pages')
        self.assertIn('max_id=cursor-2', sent.call_args.kwargs['data'])

    def test_bad_ids_and_blocked_replies(self):
        with self.assertRaises(instagram.UnexpectedResponse):
            instagram.audio_reels('../etc')
        blocked = MagicMock(status_code=429)
        with patch.object(instagram, 'request', side_effect=[self.page, blocked]), \
             patch.object(instagram.BACKOFF, 'hit'), self.assertRaises(instagram.RateLimited):
            instagram.audio_reels(AUDIO)


class SoundPageTests(unittest.TestCase):
    def setUp(self):
        server.SOUND_STATE.clear()

    def request(self, method, path, body=None):
        handler = Handler.__new__(Handler)
        handler.path = path
        raw = json.dumps(body or {}).encode()
        handler.headers = {'Content-Length': str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        with patch.object(handler, '_send') as send, patch.object(handler, '_allowed', return_value=True), \
             patch.object(views, 'feed_accounts', return_value=set()):
            (handler.do_GET if method == 'GET' else handler.do_POST)()
        return send.call_args.args

    def test_page_header_and_next_page(self):
        first = {'title': 'Wiz Khalifa | We Dem Boyz', 'total': '41K reels', 'reels': [instagram.slim(reel('AAAAAAAAAAA'))],
                 'cursor': 'cursor-2', 'has_more': True}
        with patch.object(instagram, 'audio_reels', return_value=first):
            status, body = self.request('GET', f'/sound/{AUDIO}')[:2]
        page = body.decode()
        self.assertEqual(status, 200)
        self.assertIn('<strong>We Dem Boyz</strong><p>Wiz Khalifa</p>', page)
        self.assertIn('41K reels on Instagram', page)
        self.assertIn(f'https://www.instagram.com/reels/audio/{AUDIO}/', page)
        second = {**first, 'reels': [instagram.slim(reel('BBBBBBBBBBB'))], 'cursor': None, 'has_more': False}
        with patch.object(instagram, 'audio_reels', return_value=second) as fetch:
            status, body = self.request('POST', f'/sound/{AUDIO}/more')[:2]
        self.assertEqual(fetch.call_args.kwargs['after'], 'cursor-2')
        self.assertEqual([p['shortcode'] for p in json.loads(body)['posts']], ['BBBBBBBBBBB'])
        status, body = self.request('POST', f'/sound/{AUDIO}/more')[:2]
        self.assertEqual(json.loads(body), {'posts': [], 'has_more': False}, 'no more pages, no request')

    def test_original_audio_links_its_creator_and_text_is_escaped(self):
        header = views._sound_summary({'id': AUDIO, 'title': 'rodrygogoes | Original audio', 'total': ''})
        self.assertIn('Original audio by <a href="/u/rodrygogoes">@rodrygogoes</a>', header)
        self.assertNotIn('on Instagram</p>', header, 'no count when Instagram gives none')
        header = views._sound_summary({'id': AUDIO, 'title': '<b>x</b> | <i>y</i>', 'total': '<script>'})
        self.assertNotIn('<b>', header)
        self.assertNotIn('<script>', header)

    def test_videos_on_a_sound_page_load(self):
        with patch.object(Handler, '_media', return_value={'video': 'https://cdn.test/v.mp4'}):
            status, body = self.request('POST', f'/sound/{AUDIO}/media', {'code': 'AAAAAAAAAAA'})[:2]
        self.assertEqual((status, json.loads(body)['video']), (200, 'https://cdn.test/v.mp4'))

    def test_unavailable_sound_is_a_safe_error(self):
        with patch.object(instagram, 'audio_reels', side_effect=instagram.ProfileUnavailable('This sound is unavailable')):
            status = self.request('GET', f'/sound/{AUDIO}')[0]
        self.assertGreaterEqual(status, 400)
        self.assertEqual(self.request('GET', '/sound/notanumber')[0], 404)


class SongInfoTests(unittest.TestCase):
    def test_licensed_track_and_original_audio(self):
        licensed = {'clips_metadata': {'music_info': {'music_asset_info': {'title': 'We Dem Boyz', 'display_artist': 'Wiz Khalifa'}},
                                       'original_sound_info': None}}
        self.assertEqual(instagram.song_info(licensed), {'title': 'We Dem Boyz', 'artist': 'Wiz Khalifa', 'original': False})
        original = {'clips_metadata': {'music_info': None, 'original_sound_info': {
            'original_audio_title': 'Tag dost\n.\n.\nfunny   video', 'ig_artist': {'username': 'Stheno15'}}}}
        self.assertEqual(instagram.song_info(original), {'title': 'Tag dost . . funny video', 'artist': 'stheno15', 'original': True})

    def test_missing_or_malformed_audio(self):
        for media in (None, {}, {'clips_metadata': 'x'}, {'clips_metadata': {'music_info': {'music_asset_info': {'title': ''}}}},
                      {'clips_metadata': {'original_sound_info': {'ig_artist': {'username': '../bad'}}}}):
            self.assertIsNone(instagram.song_info(media), media)

    def test_reel_details_carry_the_song(self):
        media = {'code': 'AAAAAAAAAAA', 'user': {'username': 'someone'},
                 'clips_metadata': {'music_info': {'music_asset_info': {'title': 'Song', 'display_artist': 'Band', 'audio_cluster_id': AUDIO}}}}
        with patch.object(instagram, '_post_media', return_value=media):
            info = instagram.media_info('AAAAAAAAAAA')
        self.assertEqual((info['song']['title'], info['audio_id']), ('Song', AUDIO))
