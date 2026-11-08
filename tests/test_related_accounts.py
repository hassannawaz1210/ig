"""Instagram's "Accounts you might like" rail, without network access."""
import io
import json
import unittest
from unittest.mock import MagicMock, patch

from ig import instagram
from ig.server import Handler


def profile_page(pk='42'):
    payload = {'require': [{'xig_user_by_username': {'pk': pk, 'username': 'star', 'follower_count': 5}},
                           {'items': [{'pk': '999', 'code': 'POST'}]}]}
    return ('<script>"LSD",[],{"token":"lsd-token"}</script>'
            f'<script type="application/json" data-sjs>{json.dumps(payload)}</script>')


def reply(nodes):
    response = MagicMock()
    response.json.return_value = {'data': {'xig_user_by_igid_v2': {'ayml_logged_out': nodes}}}
    return response


class RelatedAccountsTests(unittest.TestCase):
    def setUp(self):
        instagram._AYML_CACHE.clear()

    def suggestions(self, nodes, page=None):
        session = MagicMock()
        session.cookies.get.return_value = 'csrf'
        with patch.object(instagram, 'fetch_profile_page', return_value=(session, page or profile_page())), \
             patch.object(instagram, 'request', return_value=reply(nodes)) as sent:
            return instagram.related_accounts('Star'), sent

    def test_uses_the_profile_id_and_keeps_safe_fields(self):
        accounts, sent = self.suggestions([
            {'username': 'Friend.One', 'full_name': 'Friend', 'is_verified': 1, 'profile_pic_url': 'https://cdn.test/a.jpg'},
            {'username': 'bad name!', 'full_name': 'x'},
            {'username': 'star'},
            {'username': 'other', 'profile_pic_url': 'javascript:alert(1)'},
            'not a dict'])
        self.assertIn('%22id%22%3A%2242%22', sent.call_args.kwargs['data'], "the profile's id, not a post's")
        self.assertEqual(accounts, [
            {'username': 'friend.one', 'full_name': 'Friend', 'is_verified': True, 'profile_pic_url': 'https://cdn.test/a.jpg'},
            {'username': 'other', 'full_name': '', 'is_verified': False, 'profile_pic_url': None}])

    def test_results_are_cached(self):
        self.suggestions([{'username': 'friend'}])
        with patch.object(instagram, 'fetch_profile_page') as fetch:
            self.assertEqual(instagram.related_accounts('star')[0]['username'], 'friend')
        fetch.assert_not_called()

    def test_missing_tokens_and_blocked_replies(self):
        with self.assertRaises(instagram.UnexpectedResponse):
            self.suggestions([], page='<html>no tokens</html>')
        session = MagicMock()
        blocked = MagicMock(); blocked.json.return_value = {'require_login': True}
        with patch.object(instagram, 'fetch_profile_page', return_value=(session, profile_page())), \
             patch.object(instagram, 'request', return_value=blocked), \
             patch.object(instagram.BACKOFF, 'hit'), self.assertRaises(instagram.RateLimited):
            instagram.related_accounts('star')


class RelatedRouteTests(unittest.TestCase):
    def get(self, path):
        handler = Handler.__new__(Handler)
        handler.path = path
        handler.headers = {}
        handler.rfile = io.BytesIO()
        with patch.object(handler, '_send') as send, patch.object(handler, '_allowed', return_value=True):
            handler.do_GET()
        return send.call_args.args if send.called else None

    def test_route_returns_accounts_and_safe_errors(self):
        with patch.object(instagram, 'related_accounts', return_value=[{'username': 'friend'}, {'username': 'Kept'}]), \
             patch('ig.server.feed_accounts', return_value={'kept'}):
            status, body = self.get('/related/Star')[:2]
        self.assertEqual((status, json.loads(body)), (200, {'accounts': [
            {'username': 'friend', 'in_feed': False}, {'username': 'Kept', 'in_feed': True}]}),
            'suggestions already in a feed say so from the start')
        with patch.object(instagram, 'related_accounts', side_effect=instagram.RateLimited('slow down')):
            status = self.get('/related/star')[0]
        self.assertGreaterEqual(status, 400)


class ProfileViewsTests(RelatedRouteTests):
    def test_views_come_from_the_reels_tab_page_by_page(self):
        from ig import server
        server.VIEW_CONTEXTS.clear()
        page = {'reels': [{'shortcode': 'AAAAAAAAAAA', 'play_count': 2816256}, {'shortcode': 'BBBBBBBBBBB', 'play_count': None}],
                'cursor': 'next=1', 'has_more': True}
        with patch.object(instagram, 'reels_tab', return_value=page) as tab:
            status, body = self.get('/u/Star/views')[:2]
            self.assertEqual((status, json.loads(body)), (200, {'views': {'AAAAAAAAAAA': 2816256}, 'count': 2,
                                                                 'cursor': 'next=1', 'has_more': True}))
            self.get('/u/star/views?after=next%3D1')
        self.assertEqual(tab.call_args.kwargs['after'], 'next=1')
        self.assertIs(tab.call_args_list[0].kwargs['context'], tab.call_args_list[1].kwargs['context'],
                      'one Instagram session per creator while paging')

    def test_views_failures_are_safe(self):
        with patch.object(instagram, 'reels_tab', side_effect=instagram.RateLimited('Instagram is limiting logged-out browsing')):
            status, body = self.get('/u/star/views')[:2]
        self.assertGreaterEqual(status, 400)
        self.assertIn(b'limiting', body, 'expected failures explain themselves')
        with patch.object(instagram, 'reels_tab', side_effect=RuntimeError('database is locked /home/x')):
            status, body = self.get('/u/star/views')[:2]
        self.assertGreaterEqual(status, 500)
        self.assertNotIn(b'/home/x', body, 'unexpected failures hide internals')
