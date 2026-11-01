from unittest.mock import patch
from ig import instagram, recommendations as rec, config, feeds
from ig.reasons import Reason
from test_ig import ProjectTests


class CollaborationLeadTests(ProjectTests):
    def test_html_media_extracts_handles_without_another_request(self):
        media={'code':'clip001','user':{'username':'owner'},
               'coauthor_producers':[{'username':'Editor'},{'username':'owner'},
                   {'username':'hidden','is_unpublished':True},{'username':'bad/name'},None],
               'clips_metadata':{'original_sound_info':{'ig_artist':{'username':'Composer'}}},
               'is_paid_partnership':True}
        with patch.object(instagram,'_post_media',return_value=media) as fetch:
            result=instagram.media_info('clip001')
        fetch.assert_called_once_with('clip001')
        self.assertEqual(result['coauthors'],['editor'])
        self.assertEqual(result['audio_creator'],'composer')
        self.assertTrue(result['paid_partnership'])
        media['clips_metadata']['original_sound_info']['ig_artist']['username']='owner'
        with patch.object(instagram,'_post_media',return_value=media):
            self.assertEqual(instagram.media_info('clip001')['audio_creator'],'')

    def test_missing_attribution_is_safe(self):
        for value in [None,{},'bad']:
            with patch.object(instagram,'_post_media',return_value={'coauthor_producers':value,'clips_metadata':value}):
                data=instagram.media_info('clip001')
                self.assertEqual(data['coauthors'],[])
                self.assertEqual(data['audio_creator'],'')

    def test_leads_persist_and_respect_source_scope_blocks_and_sponsor_strength(self):
        post={'shortcode':'clip001','owner':'creator','coauthors':['editor'],
              'audio_creator':'composer'}
        rec.remember_reels([post])
        reasons={}
        leads=rec.related_leads({'creator'},reasons=reasons)
        self.assertGreater(leads['editor'],leads['composer'])
        self.assertEqual(reasons['editor']['source'],'coauthor')
        self.assertEqual(rec.related_leads({'unrelated'}),{})
        self.assertIn('editor',rec.related_leads({'creator'}))
        config.set_blocked('editor',True)
        pool=feeds.make_pool([])
        feeds.discover_into(pool,leads)
        self.assertNotIn('editor',pool['discovered'])
        self.assertIn('composer',pool['discovered'])
        sponsored=rec.candidate_lead_kinds(dict(post,paid_partnership=True))
        self.assertLess(sponsored['editor'][0],1)
        self.assertLess(sponsored['composer'][0],leads['composer'])
        self.assertNotIn('editor',rec.discovery_accounts(),'a collaboration is not a like')

    def test_saved_coauthor_can_supply_leads_without_saving_primary_owner(self):
        rec.remember_reels([{'shortcode':'clip001','owner':'primary','coauthors':['saved','neweditor']}])
        self.assertIn('neweditor',rec.related_leads({'saved'}))
        self.assertNotIn('saved',rec.related_leads({'saved'}))

    def test_stronger_evidence_wins_and_self_attribution_is_ignored(self):
        post={'owner':'creator','coauthors':['editor','creator'],'audio_creator':'editor',
              'caption':'credit @editor'}
        self.assertEqual(rec.candidate_lead_kinds(post)['editor'],(2.5,'coauthor'))
        self.assertNotIn('creator',rec.candidate_lead_kinds(post))
        reason=Reason('editor');reason.lead={'source':'coauthor','via':'creator'}
        self.assertIn('collaborated',reason.as_dict()['details'][0])

    def test_updated_attribution_clears_old_leads(self):
        rec.remember_reels([{'shortcode':'clip001','owner':'creator','coauthors':['old'],'audio_creator':'audio'}])
        rec.remember_reels([{'shortcode':'clip001','coauthors':[],'audio_creator':''}])
        self.assertEqual(rec.related_leads({'creator'}),{})

    def test_collaboration_does_not_bypass_topic_checks(self):
        config.save_feeds({'memes':['creator']})
        rec.remember_reels([{'shortcode':'source1','owner':'creator','coauthors':['editor']}])
        states={
            'creator':{'posts':[],'has_more':False},
            'editor':{'biography':'Python programming and software engineering',
                      'posts':[{'shortcode':'coding1','owner':'editor','is_video':True,'caption':'Python software tutorial'}],
                      'has_more':False}}
        with patch('ig.feeds.STATE',states), patch('ig.feeds.random.random',return_value=1):
            result=feeds._pool_more(feeds.make_pool(['creator']),allow_network=False)
        self.assertEqual(result['posts'],[])
        self.assertEqual(config.load_feeds(),{'memes':['creator']})
