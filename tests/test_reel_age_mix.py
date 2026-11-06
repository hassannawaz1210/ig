import random
import unittest
from unittest.mock import patch
from ig import recommendations as rec


class ReelAgeMixTests(unittest.TestCase):
    def test_preferences_rank_within_the_chosen_group(self):
        posts = [
            {'shortcode':'recent', 'caption':'favorite favorite'},
            {'shortcode':'old-low', 'caption':'', 'reel_age_group':'older'},
            {'shortcode':'old-liked', 'caption':'favorite', 'reel_age_group':'older'},
        ]
        with patch.object(rec.random, 'random', side_effect=[.1, 1]):
            picked, exploratory = rec.pick_feed_reel(posts, ({'word:favorite':1}, {}))
        self.assertEqual(picked['shortcode'], 'old-liked')
        self.assertFalse(exploratory)
        with patch.object(rec.random, 'random', side_effect=[.9, 1]):
            self.assertEqual(rec.pick_feed_reel(posts, ({}, {}))[0]['shortcode'], 'recent')

    def test_missing_groups_fall_back_without_discarding_candidates(self):
        for posts in [[{'shortcode':'legacy'}], [{'shortcode':'older', 'reel_age_group':'older'}]]:
            with patch.object(rec, 'pick_reel', return_value=(posts[0], False)) as rank:
                self.assertEqual(rec.pick_feed_reel(posts, ({}, {}))[0], posts[0])
                self.assertEqual(rank.call_args.args[0], posts)

    def test_mix_is_about_thirty_percent_older_when_both_are_available(self):
        rng = random.Random(42)
        posts = [{'shortcode':'recent'}, {'shortcode':'old','reel_age_group':'older'}]
        with patch.object(rec.random, 'random', side_effect=rng.random), \
                patch.object(rec, 'pick_reel', side_effect=lambda candidates, *a, **kw: (candidates[0], False)):
            count = sum(rec.pick_feed_reel(posts, ({}, {}))[0]['shortcode']=='old' for _ in range(1000))
        self.assertTrue(260 <= count <= 340, count)

    def test_view_baseline_uses_full_reference_not_selected_group(self):
        posts = [{'shortcode':'new'}, {'shortcode':'old','reel_age_group':'older'}]
        with patch.object(rec.random, 'random', return_value=.1), \
                patch.object(rec, 'pick_reel', return_value=(posts[1], False)) as rank:
            rec.pick_feed_reel(posts, ({}, {}), view_reference=posts)
        self.assertIs(rank.call_args.kwargs['view_reference'], posts)

from test_profile_cache import TempDirs
from ig import config, feeds


class ReelAgeFeedTests(TempDirs):
    def test_old_watched_and_disliked_reels_are_removed_before_age_choice(self):
        config.save_feeds({'edits':['creator']})
        posts = [{'shortcode':code, 'owner':'creator', 'is_video':True,
                  'reel_age_group':group} for code, group in
                 [('watched','older'), ('disliked','older'), ('available','recent')]]
        rec.remember_reels(posts)
        rec.set_feedback('disliked','dislike')
        pool = feeds.make_pool(['creator'])
        pool['served'].add('watched')
        with patch('ig.feeds.STATE', {'creator':{'posts':posts,'has_more':False}}), \
                patch.object(rec, 'pick_feed_reel', wraps=rec.pick_feed_reel) as pick:
            result = feeds._pool_more(pool, allow_network=False)
        self.assertEqual([p['shortcode'] for p in result['posts']], ['available'])
        self.assertEqual([p['shortcode'] for p in pick.call_args.args[0]], ['available'])

    def test_older_content_does_not_bypass_blocks_or_selected_feeds(self):
        config.save_feeds({'edits':['creator'], 'music':['other']})
        config.set_selected_feeds(['edits'])
        config.set_blocked('creator', True)
        states = {name:{'posts':[{'shortcode':name,'owner':name,'is_video':True,
                  'reel_age_group':'older'}],'has_more':False} for name in ['creator','other']}
        with patch('ig.feeds.STATE', states), patch.object(rec, 'pick_feed_reel') as pick:
            result = feeds._pool_more(feeds.make_pool(['creator','other']), allow_network=False)
        self.assertEqual(result['posts'], [])
        pick.assert_not_called()
