from unittest.mock import patch
from ig import config, feeds, recommendations as rec
from ig.refill import Refill
from test_profile_cache import TempDirs


class DiscoveryExperimentTests(TempDirs):
    def test_healthy_saved_queue_reserves_one_worker_for_new_creator(self):
        refill=Refill()
        self.addCleanup(refill.executor.shutdown, wait=True)
        states={n:{'posts':[{'shortcode':n+str(i),'is_video':True} for i in range(8)],'has_more':True}
                for n in ['saved1','saved2','saved3']}
        with patch('ig.refill.REELS.cached',return_value=None), patch('ig.refill.REELS.load',return_value=({},True)), patch('ig.refill.more_reels',return_value={}):
            refill.schedule({'served':set()}, [*states,'new'],set(states),states=states)
            self.assertIn('new',refill.pending)
            self.assertEqual(len(refill.pending),2)
            for future,_ in refill.pending.values(): future.result()
            refill.collect({},lambda *args:None)
            refill.schedule({'served':set()}, [*states,'another'],set(states),states=states)
            self.assertNotIn('another',refill.pending,'discovery inspection cadence is bounded')

    def test_empty_saved_queue_gets_both_workers(self):
        refill=Refill()
        self.addCleanup(refill.executor.shutdown, wait=True)
        with patch('ig.refill.REELS.cached',return_value=None),patch('ig.refill.REELS.load',return_value=({},True)):
            refill.schedule({'served':set()},['a','b','trial'],{'a','b'},states={})
            self.assertEqual(set(refill.pending),{'a','b'})
            for future,_ in refill.pending.values(): future.result()

    def test_liked_reel_expands_only_matching_selected_topic(self):
        rec.remember_reels([{'shortcode':'liked1','owner':'unknown','caption':'#gaming @gameeditor'},
                            {'shortcode':'liked2','owner':'other','caption':'#gym @gymcoach'}])
        rec.set_feedback('liked1','like');rec.set_feedback('liked2','like')
        leads=rec.related_leads({'saved'},topics={'gaming':['saved']})
        self.assertIn('gameeditor',leads)
        self.assertNotIn('gymcoach',leads)

    def test_only_watched_trials_count_and_completion_is_not_approval(self):
        posts=[{'shortcode':str(i),'owner':'creator'+str(i),'discovery_source':'credit'} for i in range(10)]
        rec.remember_reels(posts)
        self.assertEqual(rec.discovery_results(),{})
        for i in range(9): rec.record_view(str(i),'creator'+str(i),'event'+str(i))
        rec.record_view('0','creator0','event0')
        result=rec.discovery_results()['credit']
        self.assertEqual(result,{'watched':9,'likes':0,'dislikes':0,'creators':9})
        self.assertEqual(rec.discovery_weights({'new':{'source':'credit'}})['new'],1)
        for i in range(8): rec.set_feedback(str(i),'dislike')
        self.assertGreater(rec.discovery_weights({'new':{'source':'credit'}})['new'],0)
        self.assertLess(rec.discovery_weights({'new':{'source':'credit'}})['new'],1)

    def test_spacing_uses_watched_and_queued_owners(self):
        for i in range(2): rec.record_view(str(i),'saved','event'+str(i))
        self.assertEqual(rec.saved_streak({'saved'},['saved']),3)
        self.assertEqual(rec.saved_streak({'saved'},['new','saved']),1)

    def test_trial_takes_fourth_slot_without_changing_memberships(self):
        config.save_feeds({'gaming':['saved1','saved2','saved3']})
        states={n:{'posts':[{'shortcode':n+'clip'+str(i),'owner':n,'is_video':True,'caption':'gaming edit'} for i in range(3)], 'has_more':False}
                for n in ['saved1','saved2','saved3','new']}
        pool=feeds.make_pool(['new']);pool['saved_streak']=0
        with patch('ig.feeds.STATE',states),patch.object(feeds.REELS.cache,'fresh_usernames',return_value=set()):
            batch=feeds._pool_more(pool,allow_network=False)
        self.assertEqual(len(batch['posts']),4)
        self.assertEqual(batch['posts'][3]['owner'],'new')
        self.assertIn('discovery_source',batch['posts'][3])
        self.assertEqual(config.load_feeds(),{'gaming':['saved1','saved2','saved3']})
