import json
from unittest.mock import patch
from ig import config, feeds, history, recommendations
from test_viewing_history import ViewingHistoryTests


class CreatorBrowsingTests(ViewingHistoryTests):
    def test_explicit_browsing_stays_on_creator_and_respects_exclusions(self):
        posts=[dict(self.post('clip'+str(i)),owner='creator') for i in range(6)]
        posts.append(dict(self.post('other01'),owner='other'))
        states={'creator':{'posts':posts,'has_more':False}}
        with patch('ig.feeds.STATE',states):
            first=feeds.creator_more('creator',client='client-one',exclude_codes=['clip0'])
            self.assertEqual([p['shortcode'] for p in first['posts']],['clip1'])
            again=feeds.creator_more('creator',client='client-one',exclude_codes=['clip0'])
            self.assertEqual([p['shortcode'] for p in again['posts']],['clip2'])
            third=feeds.creator_more('creator',client='client-one',exclude_codes=['clip0'])
            self.assertEqual([p['shortcode'] for p in third['posts']],['clip3'])
        self.assertEqual(config.load_feeds(),{'custom':['creator']})
        self.assertEqual(history.load_history()['codes'],[])

    def test_creator_route_honors_blocks_and_validates_input(self):
        config.set_blocked('creator',True)
        self.assertEqual(self.request('/creator/creator/more',{'client':'client-one'}),200)
        with patch('ig.feeds.REFILL.schedule') as schedule:
            self.assertEqual(feeds.creator_more('creator',client='client-one')['posts'],[])
            schedule.assert_not_called()
        self.assertEqual(self.request('/creator/creator/more',{'client':'bad'}),400)
