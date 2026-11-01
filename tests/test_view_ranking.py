import unittest
from unittest.mock import patch
from ig import recommendations as rec
from ig.reasons import Reason


def reel(code, views, owner='creator', caption=''):
    return {'shortcode':code,'owner':owner,'play_count':views,'caption':caption}


class ViewRankingTests(unittest.TestCase):
    def test_equal_relative_performance_has_equal_boost_for_small_and_large_creators(self):
        small = [reel(str(i), n) for i,n in enumerate([100,100,100,200,400])]
        large = [reel('big'+str(i), n*10000, owner='large') for i,n in enumerate([100,100,100,200,400])]
        bases = rec.view_baselines(small+large)
        self.assertEqual(rec.view_count_boost(small[-1],bases),rec.view_count_boost(large[-1],bases))
        self.assertAlmostEqual(rec.view_count_boost(small[-2],bases),rec.MAX_VIEW_BOOST/2)
        self.assertEqual(rec.view_count_boost(reel('huge',10**100),bases),rec.MAX_VIEW_BOOST)

    def test_missing_bad_low_and_small_samples_are_neutral(self):
        refs=[reel(str(i),100) for i in range(5)]
        bases=rec.view_baselines(refs)
        for value in [None,True,'10000',float('inf'),-1,0,50,100]:
            self.assertEqual(rec.view_count_boost(reel('x',value),bases),0)
        self.assertEqual(rec.view_baselines(refs[:4]),{})
        self.assertEqual(rec.view_baselines([refs[0]]*10),{})
        self.assertEqual(rec.view_count_boost(reel('x',100),{'creator':0}),0)

    def test_popularity_breaks_tie_but_does_not_overrule_strong_preference(self):
        refs=[reel(str(i),100) for i in range(5)]
        regular=reel('regular',100)
        popular=reel('popular',100000)
        preferred=reel('preferred',None,caption='cinematography')
        with patch.object(rec.random,'random',return_value=1):
            self.assertEqual(rec.pick_reel([regular,popular],({},{}),view_reference=refs)[0],popular)
            self.assertEqual(rec.pick_reel([preferred,popular],({'word:cinematography':1},{}),
                                          view_reference=refs)[0],preferred)

    def test_exploration_is_unaffected(self):
        low=reel('low',1);high=reel('high',100000)
        with patch.object(rec.random,'random',return_value=0),patch.object(rec.random,'choice',return_value=low):
            self.assertEqual(rec.pick_reel([low,high],({},{})),(low,True))

    def test_explanation_only_claims_boost_for_ranked_picks(self):
        reason=Reason('creator');reason.view_boost=True
        self.assertTrue(any('median' in line for line in reason.as_dict()['details']))
        reason.exploratory=True
        self.assertFalse(any('median' in line for line in reason.as_dict()['details']))
