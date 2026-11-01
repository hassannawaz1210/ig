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

    def test_unrelated_likes_do_not_supply_hashtag_leads(self):
        rec.remember_reels([
            {'shortcode':'movie','owner':'film','caption':'#cinema #halloween'},
            {'shortcode':'another','owner':'actor','caption':'#cinema #halloween'},
            {'shortcode':'tech','owner':'saved','caption':'#programming'}])
        rec.set_feedback('movie','like')
        self.assertNotIn('actor',rec.related_leads({'saved'},topics={'programming':['saved']}))

    def test_topic_checks_include_hashtags_below_intro(self):
        state={'posts':[{'is_video':True,'caption':'Look at this!\n\n#programming #python'} for _ in range(3)]}
        self.assertIn('programming',rec.matching_topics(state,['programming']))

    def test_strong_credit_beats_large_commenter_pool_for_inspection(self):
        refill=Refill();self.addCleanup(refill.executor.shutdown,wait=True)
        names=['credited']+['fan'+str(i) for i in range(50)]
        pool={'served':set(),'buffered':8,'discovery_weights':dict.fromkeys(names,.15)}
        pool['discovery_weights']['credited']=2.5
        with patch('ig.refill.random.random',return_value=.1),patch('ig.refill.REELS.cached',return_value=None),patch('ig.refill.REELS.load',return_value=({},True)):
            refill.schedule(pool,names,set(),states={})
            self.assertEqual(set(refill.pending),{'credited'})
            for future,_ in refill.pending.values():future.result()

    def test_inconclusive_creator_gets_more_samples_before_rejection(self):
        refill=Refill();self.addCleanup(refill.executor.shutdown,wait=True)
        state={'posts':[{'shortcode':'one','is_video':True}],'pending':[{'shortcode':'two'}],'has_more':True}
        with patch('ig.refill.more_reels',return_value={}) as paging:
            refill.schedule({'served':set()},['trial'],set(),states={'trial':state})
            for future,_ in refill.pending.values():future.result()
        paging.assert_called_once_with('trial')

    def test_watching_reels_does_not_reset_creator_streak_cap(self):
        from ig import history
        config.save_feeds({'programming':['fast','slow']})
        for i in range(2):
            p=history.reserve([{'shortcode':'seen'+str(i),'owner':'fast'}],'client-test')[0]
            history.record_playback(p['shortcode'],p['reservation'],'client-test',1)
        states={'fast':{'posts':[{'shortcode':'next','owner':'fast','is_video':True}],'has_more':True},
                'slow':{'posts':[],'has_more':True}}
        with patch('ig.feeds.STATE',states),patch.object(feeds.REFILL,'schedule'),patch.object(feeds.REFILL,'collect'),patch.object(feeds.REFILL,'waiting',return_value=True):
            result=feeds.pool_more(feeds.make_pool([]),client='client-test')
            self.assertEqual(result['posts'],[])
            self.assertTrue(result['has_more'])
            states['slow']['has_more']=False
            result=feeds.pool_more(feeds.make_pool([]),client='client-test')
            self.assertEqual(result['posts'][0]['owner'],'fast','exhausted alternatives must not deadlock playback')
