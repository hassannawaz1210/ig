import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ig import config, recommendations as rec, feeds


class TagIndexTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        path = Path(self.directory.name)
        for target, value in [('ig.config.CONFIG_DIR', path)]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        config.save_feeds({'memes': ['saved']})

    def store(self, *posts):
        rec.remember_reels(list(posts))

    def test_captions_build_the_tag_index(self):
        self.store({'shortcode': 'a1', 'owner': 'saved', 'caption': 'one #gymmemes #funny'},
                   {'shortcode': 'a2', 'owner': 'other', 'caption': 'also #gymmemes here'})
        reels = rec.tag_reels('gymmemes')
        self.assertEqual({r['owner'] for r in reels}, {'saved', 'other'})
        self.assertIn(('gymmemes', 2, 2), [(t, c, n) for t, c, n in rec.top_tags()])

    def test_late_caption_enrichment_reindexes_the_same_reel(self):
        self.store({'shortcode': 'a1', 'owner': 'saved'})
        self.assertEqual(rec.tag_reels('latertag'), [])
        self.store({'shortcode': 'a1', 'owner': 'saved', 'caption': 'now #latertag'})
        self.assertEqual(len(rec.tag_reels('latertag')), 1)

    def test_tags_are_lowercase_and_deduplicated(self):
        self.store({'shortcode': 'a1', 'owner': 'saved', 'caption': '#Tag #tag #TAG #x'})
        reels = rec.tag_reels('tag')
        self.assertEqual(len(reels), 1)

    def test_liked_tags_rank_liked_reel_hashtags(self):
        self.store({'shortcode': 'a1', 'owner': 'saved', 'caption': '#good #fine'},
                   {'shortcode': 'a2', 'owner': 'saved', 'caption': '#good'})
        rec.set_feedback('a1', 'like')
        self.assertEqual(rec.liked_tags(), [('good', 1), ('fine', 1)])


class TagLeadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        path = Path(self.directory.name)
        for target, value in [('ig.config.CONFIG_DIR', path)]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        config.save_feeds({'memes': ['saved']})

    def store(self, *posts):
        rec.remember_reels(list(posts))

    def test_shared_hashtag_recruits_creator_as_a_lead(self):
        self.store({'shortcode': 'src', 'owner': 'saved', 'caption': '#gymmemes daily'},
                   {'shortcode': 'one', 'owner': 'tagmate', 'caption': '#gymmemes'},
                   {'shortcode': 'two', 'owner': 'tagmate', 'caption': '#gymmemes again'})
        leads = rec.related_leads({'saved'})
        self.assertIn('tagmate', leads)
        self.assertGreater(leads['tagmate'], 0.2)   # stronger than a sponsor mention
        self.assertLess(leads['tagmate'], 2.0)      # weaker than an explicit credit

    def test_generic_tags_never_recruit(self):
        self.store({'shortcode': 'src', 'owner': 'saved', 'caption': '#funny #fyp'},
                   {'shortcode': 'one', 'owner': 'anyone', 'caption': '#funny #fyp'})
        self.assertNotIn('anyone', rec.related_leads({'saved'}))

    def test_disliked_source_reels_do_not_set_anchor_tags(self):
        self.store({'shortcode': 'src', 'owner': 'saved', 'caption': '#deadlifts'},
                   {'shortcode': 'one', 'owner': 'lifter', 'caption': '#deadlifts'})
        rec.set_feedback('src', 'dislike')
        self.assertNotIn('lifter', rec.related_leads({'saved'}))

    def test_saved_accounts_are_not_recruited_through_tags(self):
        self.store({'shortcode': 'src', 'owner': 'saved', 'caption': '#gymmemes'},
                   {'shortcode': 'one', 'owner': 'mate', 'caption': '#gymmemes'})
        self.store({'shortcode': 's2', 'owner': 'mate', 'caption': '#gymmemes'})
        config.save_feeds({'memes': ['saved', 'mate']})
        self.assertNotIn('mate', rec.related_leads({'saved', 'mate'}))


class TagTopicTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.cleanup_all)
        path = Path(self.directory.name)
        for target, value in [('ig.config.CONFIG_DIR', path)]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        config.save_feeds({'gym': ['lifter'], 'memes': ['comedian']})

    def cleanup_all(self):
        self.directory.cleanup()

    def store(self, *posts):
        rec.remember_reels(list(posts))

    def vocabulary(self, selected):
        return rec.topic_tag_vocabulary(selected)

    def test_feed_accounts_teach_topic_vocabulary(self):
        self.store({'shortcode': 'g1', 'owner': 'lifter', 'caption': '#gymmemes'},
                   {'shortcode': 'g2', 'owner': 'lifter', 'caption': '#gymmemes'},
                   {'shortcode': 'm1', 'owner': 'comedian', 'caption': '#shitposting'},
                   {'shortcode': 'm2', 'owner': 'comedian', 'caption': '#shitposting'})
        vocab = self.vocabulary({'gym': ['lifter'], 'memes': ['comedian']})
        self.assertIn('gymmemes', vocab['gym'])
        self.assertNotIn('gymmemes', vocab['memes'])
        self.assertIn('shitposting', vocab['memes'])

    def test_broad_tag_dominance_keeps_topic_assignment_weak(self):
        # #funny appears in both feeds' reels; it may qualify for both, but a
        # strongly gym-side tag must not qualify for memes.
        self.store({'shortcode': 'g1', 'owner': 'lifter', 'caption': '#squatlife'},
                   {'shortcode': 'g2', 'owner': 'lifter', 'caption': '#squatlife'},
                   {'shortcode': 'g3', 'owner': 'lifter', 'caption': '#squatlife'},
                   {'shortcode': 'm1', 'owner': 'comedian', 'caption': '#squatlife'})
        vocab = self.vocabulary({'gym': ['lifter'], 'memes': ['comedian']})
        self.assertIn('squatlife', vocab['gym'])
        self.assertNotIn('squatlife', vocab['memes'])

    def test_candidate_matching_topic_via_hashtags_only(self):
        vocab = {'memes': {'gymmemes'}}
        state = {'biography': '', 'posts': [
            {'is_video': True, 'caption': '#gymmemes'},
            {'is_video': True, 'caption': '#gymmemes'},
            {'is_video': True, 'caption': '#gymmemes'},
        ]}
        # word rules cannot see #gymmemes; the vocabulary can
        self.assertEqual(rec.matching_topics(state, ['memes']), set())
        self.assertEqual(rec.matching_topics(state, ['memes'], vocab), {'memes'})

    def test_two_hashtag_captions_do_not_qualify(self):
        vocab = {'memes': {'gymmemes'}}
        state = {'biography': '', 'posts': [
            {'is_video': True, 'caption': '#gymmemes'},
            {'is_video': True, 'caption': '#gymmemes'},
            {'is_video': True, 'caption': 'nothing'},
        ]}
        self.assertEqual(rec.matching_topics(state, ['memes'], vocab), set())


class TagDiscoveryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.cleanup_all)
        path = Path(self.directory.name)
        for target, value in [('ig.config.CONFIG_DIR', path)]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        config.save_feeds({'memes': ['saved']})

    def cleanup_all(self):
        self.directory.cleanup()

    def test_tag_lead_creator_gets_a_topic_matched_trial(self):
        # The saved account's reels teach the vocabulary and the anchor tag.
        rec.remember_reels([
            {'shortcode': 's1', 'owner': 'saved', 'caption': '#gymmemes'},
            {'shortcode': 's2', 'owner': 'saved', 'caption': '#gymmemes'},
            # An unrelated creator posts the same hashtag repeatedly.
            {'shortcode': 't1', 'owner': 'tagmate', 'caption': '#gymmemes'},
            {'shortcode': 't2', 'owner': 'tagmate', 'caption': '#gymmemes'},
            {'shortcode': 't3', 'owner': 'tagmate', 'caption': '#gymmemes'},
        ])
        states = {
            'saved': {'posts': [{'shortcode': 's1', 'owner': 'saved', 'is_video': True}],
                      'has_more': False},
            'tagmate': {'posts': [{'shortcode': f't{i}', 'owner': 'tagmate', 'is_video': True,
                                   'caption': '#gymmemes'} for i in (1, 2, 3)],
                        'has_more': False},
        }
        with patch('ig.feeds.STATE', states), patch('ig.feeds.random.random', return_value=1):
            result = feeds._pool_more(feeds.make_pool(['saved']))
        owners = {p['owner'] for p in result['posts']}
        self.assertIn('tagmate', owners)
        self.assertIn('saved', owners)

    def test_tag_lead_without_topic_evidence_stays_out(self):
        rec.remember_reels([
            {'shortcode': 's1', 'owner': 'saved', 'caption': '#gymmemes'},
            {'shortcode': 's2', 'owner': 'saved', 'caption': '#gymmemes'},
            {'shortcode': 'o1', 'owner': 'offtopic', 'caption': '#gymmemes'},
            {'shortcode': 'o2', 'owner': 'offtopic', 'caption': '#gymmemes'},
        ])
        states = {
            'saved': {'posts': [{'shortcode': 's1', 'owner': 'saved', 'is_video': True}],
                      'has_more': False},
            'offtopic': {'posts': [{'shortcode': f'o{i}', 'owner': 'offtopic', 'is_video': True,
                                    'caption': 'chocolate and snacks'} for i in (1, 2)],
                         'has_more': False},
        }
        with patch('ig.feeds.STATE', states), patch('ig.feeds.random.random', return_value=1):
            result = feeds._pool_more(feeds.make_pool(['saved']))
        self.assertNotIn('offtopic', {p['owner'] for p in result['posts']})
