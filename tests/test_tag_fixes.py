import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ig import config, feeds, recommendations as rec


class TempConfig(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = patch('ig.config.CONFIG_DIR', Path(directory.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        rec._vocabulary_cache.clear()
        self.addCleanup(rec._vocabulary_cache.clear)
        config.save_feeds({'memes': ['saved']})


class TagLeadFixTests(TempConfig):
    def test_anchor_tags_are_the_most_used_not_alphabetically_first(self):
        # 60 one-off early-alphabet tags used to crowd out the real signature tag.
        filler = ' '.join(f'#aa{i:02d}' for i in range(60))
        rec.remember_reels([{'shortcode': 'src0', 'owner': 'saved', 'caption': filler}] +
                           [{'shortcode': f'src{i}', 'owner': 'saved', 'caption': '#zzsignature'} for i in (1, 2, 3)] +
                           [{'shortcode': 'other', 'owner': 'mate', 'caption': '#zzsignature'}])
        reasons = {}
        leads = rec.related_leads({'saved'}, reasons=reasons)
        self.assertIn('mate', leads)
        self.assertEqual(reasons['mate'], {'source': 'hashtag', 'tags': ['zzsignature']})

    def test_reach_bait_and_widely_spread_tags_do_not_recruit(self):
        rec.remember_reels([{'shortcode': 'src', 'owner': 'saved', 'caption': '#explorepage #everywhere'},
                            {'shortcode': 'bait', 'owner': 'bait', 'caption': '#explorepage'}] +
                           [{'shortcode': f'w{i}', 'owner': f'user{i}', 'caption': '#everywhere'} for i in range(10)])
        leads = rec.related_leads({'saved'})
        self.assertNotIn('bait', leads)
        self.assertFalse({f'user{i}' for i in range(10)} & set(leads))

    def test_disliked_candidate_reels_are_not_tag_evidence(self):
        rec.remember_reels([{'shortcode': 'src', 'owner': 'saved', 'caption': '#niche'},
                            {'shortcode': 'bad', 'owner': 'creator', 'caption': '#niche'}])
        rec.set_feedback('bad', 'dislike')
        self.assertNotIn('creator', rec.related_leads({'saved'}))

    def test_credit_reasons_name_the_source_reel_owner(self):
        rec.remember_reels([{'shortcode': 'src', 'owner': 'saved', 'caption': 'Credit @editor'}])
        reasons = {}
        rec.related_leads({'saved'}, reasons=reasons)
        self.assertEqual(reasons['editor'], {'source': 'credit', 'via': 'saved', 'liked': False})


class TagIndexFixTests(TempConfig):
    def test_edited_caption_drops_removed_tags(self):
        rec.remember_reels([{'shortcode': 'a1', 'owner': 'saved', 'caption': '#old #kept'}])
        rec.remember_reels([{'shortcode': 'a1', 'owner': 'saved', 'caption': '#kept #new'}])
        self.assertEqual(rec.tag_reels('old'), [])
        self.assertEqual(len(rec.tag_reels('kept')), 1)
        self.assertEqual(len(rec.tag_reels('new')), 1)

    def test_pruning_removes_tag_rows_but_keeps_reels_with_feedback(self):
        with patch('ig.recommendations.MAX_REELS', 2), patch('ig.recommendations.PRUNE_SLACK', 0):
            rec.remember_reels([{'shortcode': 'liked', 'owner': 'saved', 'caption': '#keep'}])
            rec.set_feedback('liked', 'like')
            for i in range(4):
                rec.remember_reels([{'shortcode': f'c{i}', 'owner': 'saved', 'caption': '#gone'}])
        self.assertEqual(len(rec.tag_reels('keep')), 1)
        self.assertLessEqual(len(rec.tag_reels('gone')), 2)
        with rec.database() as connection:
            orphans = connection.execute('SELECT COUNT(*) FROM tag_index WHERE code NOT IN (SELECT code FROM reels)').fetchone()[0]
        self.assertEqual(orphans, 0)


class VocabularyFixTests(TempConfig):
    def test_account_in_two_feeds_teaches_both(self):
        rec.remember_reels([{'shortcode': f'g{i}', 'owner': 'both', 'caption': '#gymhumor'} for i in (1, 2)])
        vocabulary = rec.topic_tag_vocabulary({'gym': ['both'], 'memes': ['both']})
        self.assertIn('gymhumor', vocabulary['gym'])
        self.assertIn('gymhumor', vocabulary['memes'])

    def test_reach_bait_never_becomes_topic_vocabulary(self):
        rec.remember_reels([{'shortcode': f'g{i}', 'owner': 'saved', 'caption': '#explorepage #niche'} for i in (1, 2)])
        vocabulary = rec.topic_tag_vocabulary({'memes': ['saved']})
        self.assertNotIn('explorepage', vocabulary['memes'])
        self.assertIn('niche', vocabulary['memes'])

    def test_vocabulary_is_cached_between_batches(self):
        with patch('ig.recommendations._build_vocabulary', return_value={'memes': {'x'}}) as build:
            first = rec.topic_tag_vocabulary({'memes': ['saved']})
            first['memes'].add('changed by caller')
            second = rec.topic_tag_vocabulary({'memes': ['saved']})
            rec.topic_tag_vocabulary({'memes': ['saved', 'another']})
        self.assertEqual(second, {'memes': {'x'}})
        self.assertEqual(build.call_count, 2, 'a different selection is rebuilt')

    def test_cache_expires(self):
        with patch('ig.recommendations._build_vocabulary', return_value={}) as build, \
                patch('ig.recommendations.time.time', side_effect=[0, rec.VOCABULARY_TTL + 1, rec.VOCABULARY_TTL + 1]):
            rec.topic_tag_vocabulary({'memes': ['saved']})
            rec.topic_tag_vocabulary({'memes': ['saved']})
        self.assertEqual(build.call_count, 2)


class ReasonTests(TempConfig):
    def batch(self, states, pool):
        with patch('ig.feeds.STATE', states), patch('ig.feeds.random.random', return_value=1):
            return {p['owner']: p['reason'] for p in feeds._pool_more(pool)['posts']}

    def test_saved_reel_names_its_feed(self):
        states = {'saved': {'posts': [{'shortcode': 'clip01', 'owner': 'saved', 'is_video': True}], 'has_more': False}}
        reason = self.batch(states, feeds.make_pool(['saved']))['saved']
        self.assertEqual(reason['summary'], 'From your Memes feed')
        self.assertIn('@saved is saved in your Memes feed.', reason['details'])

    def test_credited_creator_explains_source_and_topic(self):
        rec.remember_reels([{'shortcode': 'src01', 'owner': 'saved', 'caption': 'Credit @newmeme'}])
        states = {'saved': {'posts': [{'shortcode': 'clip01', 'owner': 'saved', 'is_video': True}], 'has_more': False},
                  'newmeme': {'biography': 'daily memes', 'has_more': False,
                              'posts': [{'shortcode': 'clip02', 'owner': 'newmeme', 'is_video': True}]}}
        reason = self.batch(states, feeds.make_pool(['saved']))['newmeme']
        self.assertEqual(reason['summary'], 'New creator for you')
        self.assertIn('@newmeme was credited in a reel by @saved.', reason['details'])
        self.assertIn('Its bio matches your Memes feed.', reason['details'])

    def test_liked_features_are_named(self):
        rec.remember_reels([{'shortcode': 'liked1', 'owner': 'saved', 'caption': '#cats'}])
        rec.set_feedback('liked1', 'like')
        states = {'saved': {'posts': [{'shortcode': 'clip01', 'owner': 'saved', 'is_video': True,
                                       'caption': 'more #cats'}], 'has_more': False}}
        reason = self.batch(states, feeds.make_pool(['saved']))['saved']
        self.assertIn('Picked because it shares #cats with reels you liked.', reason['details'])

    def test_random_picks_say_so(self):
        states = {'saved': {'posts': [{'shortcode': 'clip01', 'owner': 'saved', 'is_video': True}], 'has_more': False}}
        with patch('ig.feeds.STATE', states), patch('ig.recommendations.random.random', return_value=0):
            reason = feeds._pool_more(feeds.make_pool(['saved']))['posts'][0]['reason']
        self.assertIn('This reel was picked at random to keep your feed varied.', reason['details'])

    def test_uncertain_trial_is_labelled(self):
        rec.remember_reels([{'shortcode': 'src01', 'owner': 'saved', 'tagged_users': ['odd']}])
        states = {u: {'posts': [{'shortcode': u + '0001', 'owner': u, 'is_video': True, 'caption': 'architecture'}],
                      'has_more': False} for u in ['saved', 'odd']}
        # feeds and recommendations share random.random; 0 allows the uncertain trial.
        with patch('ig.feeds.STATE', states), patch('ig.feeds.random.random', return_value=0):
            reasons = {p['owner']: p['reason'] for p in feeds._pool_more(feeds.make_pool(['saved']))['posts']}
        self.assertEqual(reasons['odd']['summary'], 'Trying a new creator')
        self.assertTrue(any('occasional trial' in detail for detail in reasons['odd']['details']))


if __name__ == '__main__':
    unittest.main()
