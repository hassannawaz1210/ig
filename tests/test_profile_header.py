"""The profile header: account details parsed from the logged-out profile page."""
import json
import os
import shutil
import subprocess
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

from ig import instagram, views
from ig.server import Handler
from test_profile_cache import TempDirs
from test_reel_navigation import fake_posts, find_chromium


def account(**changes):
    """The full xig_user_by_username record, as instagram.com sends it logged out."""
    user = {
        'pk': '173560420', 'id': '17841401692602711', 'username': 'cristiano',
        'full_name': 'Cristiano Ronaldo', 'is_verified': True, 'is_private': False,
        'profile_pic_url': 'https://scontent.cdninstagram.com/v/t51.2885-19/avatar_s150x150.jpg',
        'biography': 'Line one\nLine two', 'follower_count': 680957365, 'following_count': 640,
        'all_media_count': None,
        'bio_links': [
            {'title': 'Herbalife Pro2Col', 'url': 'https://hrbl.me/CR7Pro2col', 'link_type': 'external'},
            {'title': 'Bad', 'url': 'javascript:alert(1)'},
            {'title': 'Perplexity', 'url': 'http://perplexity.ai/ronaldo'},
            {'title': 'WHOOP', 'url': 'http://join.whoop.com/cr7'},
            {'title': 'UR Cristiano', 'url': 'https://youtube.com/@cristiano'},
        ],
    }
    user.update(changes)
    return user


def page(user=None, meta='681M Followers, 652 Following, 4,154 Posts - See Instagram photos'):
    """A profile page shaped like the real one: the posts payload only holds the
    account IDs, and the account details sit in a separate data-sjs script."""
    timeline = {'xig_user_by_username': {'id': '17841401692602711', 'pk': '173560420'},
                'polaris_ordered_timeline_connection': {
                    'edges': [{'node': {'code': 'code0001', 'media_type': 1,
                                        'user': {'username': 'cristiano'}}}],
                    'page_info': {'end_cursor': 'next', 'has_next_page': True}}}
    scripts = [timeline] + ([{'data': {'xig_user_by_username': user}}] if user is not None else [])
    return (f'<meta property="og:description" content="{meta}" />' if meta else '') + ''.join(
        '<script type="application/json" data-sjs>' + json.dumps(blob) + '</script>' for blob in scripts
    ) + '"LSD",[],{"token":"lsdtoken"}'


class ProfileDetailsTests(TempDirs):
    def test_details_come_from_the_full_account_record(self):
        details = instagram.profile_details(page(account()))
        self.assertEqual(details['full_name'], 'Cristiano Ronaldo')
        self.assertTrue(details['verified'])
        self.assertEqual((details['followers'], details['following']), (680957365, 640))
        self.assertEqual(details['post_count'], 4154, 'logged out, the count comes from the meta description')
        self.assertEqual(details['biography'], 'Line one\nLine two')
        self.assertTrue(details['avatar'].startswith('https://scontent.cdninstagram.com/'))
        self.assertEqual([link['url'] for link in details['links']], [
            'https://hrbl.me/CR7Pro2col', 'http://perplexity.ai/ronaldo',
            'http://join.whoop.com/cr7', 'https://youtube.com/@cristiano'])
        self.assertEqual(details['links'][0]['title'], 'Herbalife Pro2Col')
        self.assertEqual(details['external_url'], 'https://hrbl.me/CR7Pro2col')

    def test_reported_post_count_and_hd_picture_are_preferred(self):
        details = instagram.profile_details(page(account(
            all_media_count=12, hd_profile_pic_url_info={'url': 'https://scontent.cdninstagram.com/hd.jpg'})))
        self.assertEqual(details['post_count'], 12)
        self.assertEqual(details['avatar'], 'https://scontent.cdninstagram.com/hd.jpg')

    def test_compact_meta_count_and_missing_meta(self):
        self.assertEqual(instagram.profile_details(page(account(), meta='1.2K Posts'))['post_count'], 1200)
        self.assertIsNone(instagram.profile_details(page(account(), meta=''))['post_count'])

    def test_old_external_url_is_used_when_there_are_no_bio_links(self):
        details = instagram.profile_details(page(account(bio_links=[], external_url='https://example.com/')))
        self.assertEqual(details['links'], [{'url': 'https://example.com/', 'title': ''}])
        unsafe = instagram.profile_details(page(account(
            bio_links=[{'url': 'ftp://x.com'}, 'not a link'], external_url='javascript:x',
            profile_pic_url='javascript:y')))
        self.assertEqual((unsafe['links'], unsafe['external_url'], unsafe['avatar']), ([], '', ''))

    def test_page_without_account_details_degrades_to_empty_values(self):
        details = instagram.profile_details(page(None, meta=''), {'id': '1'})
        self.assertEqual(details, {'full_name': '', 'avatar': '', 'biography': '', 'followers': None,
                                   'following': None, 'post_count': None, 'verified': False,
                                   'external_url': '', 'links': []})

    def test_fetch_stores_details_without_another_request(self):
        with patch.object(instagram, 'fetch_profile_page', return_value=(MagicMock(), page(account()))) as fetch:
            state = self.store.fetch('cristiano')
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(state['user_id'], '17841401692602711', 'pagination keeps the posts payload ID')
        self.assertEqual(state['profile']['full_name'], 'Cristiano Ronaldo')
        self.assertEqual(state['biography'], 'Line one\nLine two')
        self.assertEqual(self.store.cache.load('cristiano', 60)['profile'], state['profile'])

    def test_fetch_logs_when_the_page_has_no_account_details(self):
        with patch.object(instagram, 'fetch_profile_page', return_value=(MagicMock(), page(None))), \
             self.assertLogs('ig.instagram', 'WARNING') as logs:
            state = self.store.fetch('cristiano')
        self.assertIn('no account details for @cristiano', logs.output[0])
        self.assertEqual(len(state['posts']), 1, 'the posts still load')


@patch('ig.views.feed_accounts', return_value=set())
class ProfileHeaderMarkupTests(TempDirs):
    def render(self, profile, username='cristiano'):
        return views.viewer_html(f'@{username}', 'Posts', [], username, profile=profile)

    def test_header_shows_counts_name_bio_and_links(self, _accounts):
        html = self.render(instagram.profile_details(page(account())))
        for text in ('<dd>4,154</dd>', '<dt>posts</dt>', '<dd>681M</dd>', '<dt>followers</dt>',
                     '<dd>640</dd>', '<dt>following</dt>', 'Cristiano Ronaldo', 'aria-label="Verified"',
                     '<p>Line one\nLine two</p>', '>hrbl.me/CR7Pro2col</a>', 'and 3 more',
                     '>youtube.com/@cristiano</a>', 'target="_blank" rel="noopener noreferrer"', 'id="addacc"'):
            self.assertIn(text, html)
        self.assertNotIn('javascript:', html)

    def test_instagram_text_is_escaped(self, _accounts):
        html = self.render({'full_name': '<b>x</b>', 'biography': '<img src=x onerror=bad>',
                            'links': [{'url': 'https://e.com/"><script>'}]})
        self.assertIn('&lt;b&gt;x&lt;/b&gt;', html)
        self.assertIn('&lt;img src=x onerror=bad&gt;', html)
        self.assertNotIn('"><script>', html)

    def test_missing_details_leave_no_placeholder_text(self, _accounts):
        for profile in (None, {}, {'biography': ''}, {'followers': None, 'links': None, 'verified': False}):
            html = self.render(profile)
            summary = html[html.index('class="profile-summary"'):html.index('</section>')]
            self.assertNotIn('profile-counts', summary)
            for text in ('None', 'undefined', 'null', '—', '<p></p>', 'profile-links', 'Verified'):
                self.assertNotIn(text, summary)
            self.assertIn('<strong>cristiano</strong>', summary, 'the username stands in for the name')
            self.assertIn('id="addacc"', summary)

    def test_saved_reels_page_has_no_profile_header(self, _accounts):
        html = views.viewer_html('Saved reels', 'reels you saved', [], paged=False, empty_message='None yet')
        self.assertNotIn('class="profile-summary"', html)

    def test_compact_counts(self, _accounts):
        cases = {0: '0', 1: '1', 640: '640', 4154: '4,154', 9999: '9,999', 10000: '10K', 12500: '12.5K',
                 999_999: '1M', 1_000_000: '1M', 1_250_000: '1.2M', 680957365: '681M', 2_000_000_000: '2B'}
        for value, shown in cases.items():
            self.assertEqual(views.compact_count(value), shown, value)
        for value in (None, -1, '12', True, 1.5):
            self.assertIsNone(views.compact_count(value))
        self.assertIn('<dt>follower</dt>', self.render({'followers': 1}))

    def test_header_renders_on_a_phone_without_overflow(self, _accounts):
        if not shutil.which('node'):
            self.skipTest('Node.js is needed to drive the browser')
        chromium = find_chromium()
        if not chromium:
            self.skipTest('no headless Chromium found')
        details = instagram.profile_details(page(account(
            biography='Footballer ⚽\nA very long bio line without spaces: ' + 'x' * 120,
            # A picture that cannot load, to check the placeholder.
            profile_pic_url='http://127.0.0.1:9/expired.jpg')))
        state = {'posts': fake_posts(6), 'profile': details}
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with patch('ig.server.load_profile', return_value=state):
                # IG_TEST_SCREENSHOT saves the phone layout for a look by eye.
                result = subprocess.run(
                    ['node', str(Path(__file__).with_name('profile_header.cjs')), chromium,
                     f'http://127.0.0.1:{server.server_address[1]}/u/cristiano',
                     os.environ.get('IG_TEST_SCREENSHOT', '')],
                    capture_output=True, text=True, timeout=60)
        finally:
            server.shutdown()
            server.server_close()
        if result.returncode == 77:
            self.skipTest('headless Chromium could not start: ' + result.stderr[-300:])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
