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
        with patch.object(instagram, 'related_accounts', return_value=[{'username': 'friend'}]):
            status, body = self.get('/related/Star')[:2]
        self.assertEqual((status, json.loads(body)), (200, {'accounts': [{'username': 'friend'}]}))
        with patch.object(instagram, 'related_accounts', side_effect=instagram.RateLimited('slow down')):
            status = self.get('/related/star')[0]
        self.assertGreaterEqual(status, 400)
