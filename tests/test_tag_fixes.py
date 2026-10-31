import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ig import config, recommendations as rec


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
        self.assertIn('mate', rec.related_leads({'saved'}))

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


if __name__ == '__main__':
    unittest.main()
