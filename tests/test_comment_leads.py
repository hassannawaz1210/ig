import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ig import config, recommendations as rec
from ig import instagram
from ig.server import Handler


def _post_html(pk="111222333", code="AbCdEfGhIjK", owner="creator"):
    media_page = {"require": [["ScheduledServerJS", "handle", None, {"__bbox": {"require": [
        ["RelayPrefetchedStreamCache", "next", [],
         {"xig_polaris_media": {"if_not_gated_logged_out": {
             "pk": pk, "code": code, "user": {"username": owner}}}},
         "done"]]}}, None]]}
    lsd_page = {"define": [["LSD", [], {"token": "abc123DEF-_"}, 4035]]}
    return ('<html>'
            f'<script type="application/json" data-sjs>{json.dumps(media_page, separators=(",", ":"))}</script>'
            f'<script type="application/json" data-sjs>{json.dumps(lsd_page, separators=(",", ":"))}</script>'
            '</html>')


POST_HTML = _post_html()


def comments_payload(comments, more=True, cursor=None):
    edges = [{"node": {"pk": str(i), "text": text, "comment_like_count": likes,
                       "created_at": 1700000000 + i,
                       "user": {"username": username, "id": str(1000 + i)}},
              "cursor": "Q"} for i, (username, text, likes) in enumerate(comments)]
    page = {"has_next_page": more}
    if cursor:
        page["end_cursor"] = cursor
    return {"data": {"xig_polaris_media": {"comments_connection":
                                           {"edges": edges, "page_info": page}}}}


class CommentFetchTests(unittest.TestCase):
    def test_post_comments_parses_usernames_text_and_owner(self):
        payload = comments_payload([
            ("creator", "thanks @editor for the cut", 40),
            ("fan1", "hahaha", 5),
            ("fan2", "", 0),
        ])
        response = MagicMock(status_code=200)
        response.json.return_value = payload
        with patch.object(instagram, '_post_page', return_value=POST_HTML), \
                patch.object(instagram, 'request', return_value=response) as req:
            result = instagram.post_comments('AbCdEfGhIjK')
        self.assertEqual(result['owner'], 'creator')
        self.assertEqual([c['username'] for c in result['comments']],
                         ['creator', 'fan1', 'fan2'])
        self.assertTrue(result['comments'][0]['is_owner'])
        self.assertFalse(result['comments'][1]['is_owner'])
        self.assertEqual(result['comments'][0]['like_count'], 40)
        self.assertEqual([c['id'] for c in result['comments']], ['0', '1', '2'])
        # The GraphQL call carries the post's media id.
        from urllib.parse import unquote
        variables = dict(pair.split('=', 1) for pair in
                         req.call_args.kwargs['data'].split('&'))['variables']
        self.assertIn('"media_id":"111222333"', unquote(variables))

    def test_post_without_comments_is_not_an_error(self):
        payload = {"data": {"xig_polaris_media": {"comments_connection":
                                                  {"edges": [], "page_info": {}}}}}
        response = MagicMock(status_code=200)
        response.json.return_value = payload
        with patch.object(instagram, '_post_page', return_value=POST_HTML), \
                patch.object(instagram, 'request', return_value=response):
            result = instagram.post_comments('AbCdEfGhIjK')
        self.assertEqual(result['comments'], [])

    def test_pagination_cursor_round_trip(self):
        from urllib.parse import unquote
        response = MagicMock(status_code=200)
        response.json.return_value = comments_payload([("fan", "nice", 1)],
                                                      more=True, cursor='AQFPREVIOUS')
        with patch.object(instagram, '_post_page', return_value=POST_HTML), \
                patch.object(instagram, 'request', return_value=response) as req:
            result = instagram.post_comments('AbCdEfGhIjK', after='{"server_cursor":"AQHR"}')
        self.assertTrue(result['has_more'])
        self.assertEqual(result['next_cursor'], 'AQFPREVIOUS')
        variables = dict(pair.split('=', 1) for pair in
                         req.call_args.kwargs['data'].split('&'))['variables']
        self.assertIn('"after":"{\\"server_cursor\\":\\"AQHR\\"}"', unquote(variables))
        # A last page has no cursor to follow.
        response.json.return_value = comments_payload([("fan", "last", 0)], more=False)
        with patch.object(instagram, '_post_page', return_value=POST_HTML), \
                patch.object(instagram, 'request', return_value=response):
            last = instagram.post_comments('AbCdEfGhIjK', after='AQFPREVIOUS')
        self.assertFalse(last['has_more'])
        self.assertIsNone(last['next_cursor'])

    def test_blocked_or_shapeless_reply_raises_typed_error(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {"status": "fail", "require_login": True}
        with patch.object(instagram, '_post_page', return_value=POST_HTML), \
                patch.object(instagram, 'request', return_value=response):
            with self.assertRaises(instagram.RateLimited):
                instagram.post_comments('AbCdEfGhIjK')


class CommentRouteTests(unittest.TestCase):
    def handler(self, path):
        handler = Handler.__new__(Handler)
        handler.path = path
        handler._send = MagicMock()
        handler._fail = MagicMock()
        return handler

    def test_cursor_is_passed_through_and_returned(self):
        from urllib.parse import quote
        cursor = '{"server_cursor":"AQHR"}'
        handler = self.handler(f'/comments/AbCdEfGhIj?after={quote(cursor)}')
        page = {'shortcode': 'AbCdEfGhIj', 'owner': 'creator',
                'comments': [{'username': 'fan', 'text': 'nice', 'like_count': 0}],
                'next_cursor': 'AQFNEXT', 'has_more': True}
        with patch.object(instagram, 'post_comments', return_value=page) as fetch:
            handler.do_POST()
        fetch.assert_called_once_with('AbCdEfGhIj', after=cursor)
        status, body, _ = handler._send.call_args.args
        self.assertEqual(status, 200)
        self.assertEqual(__import__('json').loads(body)['next_cursor'], 'AQFNEXT')

    def test_oversized_cursor_and_bad_code_are_rejected(self):
        for path in (f'/comments/AbCdEfGhIj?after={"x" * 2001}', '/comments/bad code'):
            handler = self.handler(path)
            with patch.object(instagram, 'post_comments') as fetch:
                handler.do_POST()
            fetch.assert_not_called()
            self.assertEqual(handler._send.call_args.args[0], 400)


class CommentLeadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        path = Path(self.directory.name)
        for target, value in [('ig.config.CONFIG_DIR', path)]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        config.save_feeds({'memes': ['saved']})

    def test_owner_reply_mentions_and_commenters_are_kept(self):
        rec.remember_reels([{'shortcode': 'src', 'owner': 'saved'}])
        rec.remember_commenters('src', {
            'owner': 'saved',
            'comments': [
                {'username': 'saved', 'text': 'credit @collab', 'like_count': 9, 'is_owner': True},
                {'username': 'topfan', 'text': 'first', 'like_count': 5},
                {'username': 'quietfan', 'text': 'nice', 'like_count': 0},
                {'username': 'saved', 'text': 'made with @editor', 'like_count': 1, 'is_owner': True},
            ],
        })
        stored = rec.stored_reel('src')
        self.assertEqual(stored['reply_mentions'], ['collab', 'editor'])
        self.assertEqual(stored['commenters'], ['topfan', 'quietfan'])

    def test_commenters_capped_and_owner_never_recruits_self(self):
        rec.remember_reels([{'shortcode': 'src', 'owner': 'saved'}])
        crowd = [{'username': f'fan{i}', 'text': '', 'like_count': i} for i in range(12)]
        rec.remember_commenters('src', {'owner': 'saved', 'comments': crowd})
        stored = rec.stored_reel('src')
        self.assertEqual(len(stored['commenters']), rec.MAX_COMMENTERS_KEPT)
        # Highest-liked commenters are the ones kept.
        self.assertIn('fan11', stored['commenters'])
        self.assertNotIn('fan0', stored['commenters'])

    def test_lead_strengths_reflect_evidence_tiers(self):
        post = {'caption': '', 'owner': 'saved',
                'reply_mentions': ['collab'], 'commenters': ['fan']}
        kinds = rec.candidate_lead_kinds(post)
        self.assertEqual(kinds['collab'], (rec.REPLY_MENTION_LEAD, 'reply_mention'))
        self.assertEqual(kinds['fan'], (rec.COMMENTER_LEAD, 'commenter'))
        self.assertLess(kinds['fan'][0], 0.2)      # weaker than a sponsored mention
        self.assertLess(kinds['collab'][0], 2.0)   # weaker than an explicit credit

    def test_explicit_caption_evidence_beats_comment_evidence(self):
        post = {'caption': 'Credit @editor', 'owner': 'saved',
                'reply_mentions': ['editor'], 'commenters': []}
        kinds = rec.candidate_lead_kinds(post)
        self.assertEqual(kinds['editor'], (2.0, 'credit'))

    def test_commenters_flow_into_related_leads_with_reasons(self):
        rec.remember_reels([{'shortcode': 'src', 'owner': 'saved'}])
        rec.remember_commenters('src', {
            'owner': 'saved',
            'comments': [{'username': 'fan', 'text': 'haha', 'like_count': 3}],
        })
        reasons = {}
        leads = rec.related_leads({'saved'}, reasons=reasons)
        self.assertEqual(leads.get('fan'), rec.COMMENTER_LEAD)
        self.assertEqual(reasons['fan']['source'], 'commenter')

    def test_disliked_reel_commenters_do_not_recruit(self):
        rec.remember_reels([{'shortcode': 'src', 'owner': 'saved'}])
        rec.remember_commenters('src', {
            'owner': 'saved',
            'comments': [{'username': 'fan', 'text': 'haha', 'like_count': 3}],
        })
        rec.set_feedback('src', 'dislike')
        self.assertNotIn('fan', rec.related_leads({'saved'}))


class CommentEnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        path = Path(self.directory.name)
        for target, value in [('ig.config.CONFIG_DIR', path)]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        config.save_feeds({'memes': ['saved']})

    def test_media_response_stays_lean_and_leads_are_stored(self):
        class InlineThread:
            def __init__(self, target, **kwargs):
                self.target = target

            def start(self):
                self.target()

        media = {'shortcode': 'src', 'owner': 'saved', 'video': 'https://x.cdninstagram.com/v.mp4'}
        comments = {'owner': 'saved', 'comments': [
            {'username': 'fan', 'text': 'lol', 'like_count': 2},
            {'username': 'saved', 'text': 'thanks @editor', 'like_count': 4, 'is_owner': True},
        ]}
        with patch('ig.server.media_info', return_value=media), \
                patch.object(instagram, 'post_comments', return_value=comments) as fetch, \
                patch('ig.server.threading.Thread', InlineThread):
            data = Handler.__new__(Handler)._media('src')
        # The client payload never sees the discovery data.
        self.assertNotIn('commenters', data)
        self.assertNotIn('reply_mentions', data)
        stored = rec.stored_reel('src')
        self.assertEqual(stored['commenters'], ['fan'])
        self.assertEqual(stored['reply_mentions'], ['editor'])
        # A second media call does not fetch comments again.
        with patch('ig.server.media_info', return_value=media), \
                patch.object(instagram, 'post_comments', return_value=comments) as fetch2, \
                patch('ig.server.threading.Thread', InlineThread):
            Handler.__new__(Handler)._media('src')
        fetch2.assert_not_called()
