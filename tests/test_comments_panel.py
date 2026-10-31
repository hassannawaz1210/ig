import json
import unittest
from unittest.mock import patch
from ig.server import Handler


class CommentsPanelTests(unittest.TestCase):
    def test_comments_route_returns_comments(self):
        handler=Handler.__new__(Handler)
        handler.path='/comments/clip001'
        result={'comments':[{'username':'creator','text':'hello','like_count':2}]}
        with patch.object(handler,'_allowed',return_value=True), patch.object(handler,'_send') as send, \
             patch('ig.server.instagram.post_comments',return_value=result) as fetch:
            handler.do_POST()
        fetch.assert_called_once_with('clip001', after=None)
        self.assertEqual(send.call_args.args[0],200)
        self.assertEqual(json.loads(send.call_args.args[1]),result)

    def test_invalid_code_does_not_fetch(self):
        handler=Handler.__new__(Handler)
        handler.path='/comments/bad/code'
        with patch.object(handler,'_allowed',return_value=True), patch.object(handler,'_send') as send, \
             patch('ig.server.instagram.post_comments') as fetch:
            handler.do_POST()
        fetch.assert_not_called()
        self.assertEqual(send.call_args.args[0],400)
