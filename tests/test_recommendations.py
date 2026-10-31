import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ig import config, recommendations as rec, feeds
from ig.server import Handler


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        path = Path(self.directory.name)
        for target, value in [('ig.config.CONFIG_DIR', path)]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        config.save_feeds({'memes': ['saved']})

    def test_like_approves_creator_without_approving_tags(self):
        rec.remember_reels([{'shortcode':'liked1', 'owner':'creator',
                            'caption':'Funny gym story by @newcreator #powerlifting',
                            'tagged_users':['collaborator']}])
        self.assertEqual(rec.discovery_accounts(), set())
        rec.set_feedback('liked1', 'like')
        self.assertEqual(rec.discovery_accounts(), {'creator'})
        self.assertEqual(config.load_feeds(), {'memes':['saved']})
        rec.set_feedback('liked1', 'clear')
        self.assertEqual(rec.discovery_accounts(), set())

    def test_positive_and_negative_topic_matching(self):
        liked = {'shortcode':'liked1','owner':'one','caption':'Squats and powerlifting #strength','audio_id':'music1'}
        disliked = {'shortcode':'hated1','owner':'two','caption':'Movie scenes #cinema','audio_id':'music2'}
        rec.remember_reels([liked,disliked])
        rec.set_feedback('liked1','like')
        rec.set_feedback('hated1','dislike')
        model = rec.preference_model()
        same_topic = {'owner':'new','caption':'Better squats #strength','audio_id':'music1'}
        neutral = {'owner':'unknown','caption':'Astronomy and planets'}
        self.assertGreater(rec.score(same_topic,model), rec.score(neutral,model))
        self.assertLess(rec.score(disliked,model), rec.score(neutral,model))
        with patch('ig.recommendations.random.random', return_value=0.5):
            self.assertEqual(rec.choose_reel([disliked, neutral, same_topic],model), same_topic)

    def test_skip_is_weak_and_cannot_overwrite_explicit_feedback(self):
        post = {'shortcode':'clip01','owner':'one','caption':'Squats #strength'}
        rec.remember_reels([post]); rec.set_feedback('clip01','skip')
        weak = abs(rec.score(post,rec.preference_model()))
        rec.set_feedback('clip01','dislike')
        strong = abs(rec.score(post,rec.preference_model()))
        self.assertLess(weak,strong/10)
        self.assertEqual(rec.set_feedback('clip01','skip'),'dislike')
        rec.set_feedback('clip01','like')
        self.assertEqual(rec.set_feedback('clip01','skip'),'like')

    def test_late_metadata_and_song_enrich_existing_like(self):
        rec.remember_reels([{'shortcode':'clip01','owner':'one','caption':'Squats'}])
        rec.set_feedback('clip01','like')
        rec.remember_reels([{'shortcode':'clip01','tagged_users':['trainer'],'audio_id':'42'}])
        rec.remember_song('clip01',{'status':'matched','title':'Song','artist':'Artist'})
        liked = rec.liked_reels()[0]
        self.assertEqual(liked['caption'],'Squats')
        self.assertNotIn('trainer',rec.discovery_accounts())
        self.assertIn('song:song:artist',rec.preference_model()[0])
        self.assertIn('audio:42',rec.preference_model()[0])

    def test_one_trial_per_account_and_creator_rotation(self):
        names = ['creator'+str(i) for i in range(10)]
        states = {owner:{'posts':[{'shortcode':owner+str(i),'owner':owner,'is_video':True} for i in range(6)],
                         'has_more':False} for owner in names}
        rec.record_deliveries([{'shortcode':'previous','owner':'creator0'}])
        with patch('ig.feeds.STATE',states), patch('ig.feeds.load_feeds',return_value={'memes':names}):
            result = feeds._pool_more(feeds.make_pool(names))
        owners = [p['owner'] for p in result['posts']]
        self.assertEqual(len(owners),len(set(owners)))
        self.assertNotIn('creator0',owners)
        self.assertEqual(len(owners),6)

    def test_disliked_clip_stays_out_after_server_history_reset(self):
        rec.remember_reels([{'shortcode':'clip01','owner':'saved'}])
        rec.set_feedback('clip01','dislike')
        state = {'posts':[{'shortcode':'clip01','owner':'saved','is_video':True},
                          {'shortcode':'clip02','owner':'saved','is_video':True}], 'has_more':False}
        with patch('ig.feeds.STATE',{'saved':state}):
            result = feeds._pool_more(feeds.make_pool(['saved']))
        self.assertEqual([p['shortcode'] for p in result['posts']],['clip02'])

    def test_feedback_endpoint_does_not_edit_feeds(self):
        rec.remember_reels([{'shortcode':'clip01','owner':'creator'}])
        handler=Handler.__new__(Handler); handler.path='/feedback'
        body=json.dumps({'code':'clip01','action':'like'}).encode()
        handler.headers={'Content-Length':str(len(body))};handler.rfile=io.BytesIO(body)
        with patch.object(handler,'_send') as send:
            handler.do_POST()
            self.assertEqual(send.call_args.args[0],200)
        self.assertEqual(rec.feedback_for(['clip01']),{'clip01':'like'})
        self.assertEqual(config.load_feeds(),{'memes':['saved']})

    def test_trial_cooldown_is_released_by_liking(self):
        rec.remember_reels([{'shortcode':'trial1','owner':'newcreator'}])
        rec.record_deliveries([{'shortcode':'trial1','owner':'newcreator'}])
        self.assertIn('newcreator',rec.trial_cooldowns({'saved'}))
        rec.set_feedback('trial1','like')
        self.assertNotIn('newcreator',rec.trial_cooldowns({'saved'}))

    def test_saved_reel_credits_supply_persistent_discovery(self):
        rec.remember_reels([{'shortcode':'credit1','owner':'saved','caption':'Credit @newmeme', 'tagged_users':['another']}])
        self.assertEqual(rec.related_accounts({'saved'}), {'newmeme'})
        self.assertEqual(config.load_feeds(), {'memes':['saved']})
        rec.set_feedback('credit1','dislike')
        self.assertEqual(rec.related_accounts({'saved'}), set())

    def test_new_creator_gets_trial_even_when_random_choice_favors_saved(self):
        states = {u:{'posts':[{'shortcode':u,'owner':u,'is_video':True}], 'has_more':False}
                  for u in ['saved','newmeme']}
        states['newmeme']['biography'] = 'I make ironic memes and comedy'
        pool = feeds.make_pool(['saved'])
        feeds.discover_into(pool, ['newmeme'], meme_source=True)
        with patch('ig.feeds.STATE', states), patch('ig.feeds.random.random',return_value=1):
            result = feeds._pool_more(pool,limit=1)
        self.assertEqual(result['posts'][0]['owner'], 'newmeme')
        self.assertEqual(config.load_feeds(), {'memes':['saved']})

    def test_sponsor_mentions_and_plain_tags_are_not_leads(self):
        for caption in ['Thanks @brand', 'Collab with @brand', 'Sponsored by @brand. Credit @editor',
                        'Paid partnership with @brand', 'Credit @editor #ad']:
            self.assertEqual(rec.creator_credits({'caption':caption, 'tagged_users':['brand']}), set())
        self.assertEqual(rec.creator_credits({'caption':'Video by @editor'}), {'editor'})

    def test_credit_requires_independent_topic_evidence(self):
        rec.remember_reels([{'shortcode':'source','owner':'saved','caption':'Credit @brand'}])
        states = {u:{'posts':[{'shortcode':u,'owner':u,'is_video':True}], 'has_more':False}
                  for u in ['saved','brand']}
        states['brand']['biography'] = 'Chocolate and snacks'
        with patch('ig.feeds.STATE',states):
            result = feeds._pool_more(feeds.make_pool(['saved']))
        self.assertEqual([p['owner'] for p in result['posts']], ['saved'])

    def test_ironic_saved_memes_do_not_need_literal_topic_words(self):
        config.save_feeds({'memes':['brainrot'],'programming':['coder']})
        config.set_selected_feeds(['memes'])
        state={'posts':[{'shortcode':'irony','owner':'brainrot','caption':'Singapore gardens and modern architecture', 'is_video':True}], 'has_more':False}
        with patch('ig.feeds.STATE',{'brainrot':state}):
            result=feeds._pool_more(feeds.make_pool(['brainrot']))
        self.assertEqual([p['owner'] for p in result['posts']], ['brainrot'])

    def test_selected_feeds_exclude_other_saved_accounts_even_if_liked(self):
        config.save_feeds({'memes':['brainrot'],'programming':['coder'],'electronics':['maker']})
        config.set_selected_feeds(['programming','electronics'])
        rec.remember_reels([{'shortcode':'liked','owner':'brainrot'}])
        rec.set_feedback('liked','like')
        pool=feeds.feed_pool(feeds.MIX)
        self.assertEqual(feeds.available_accounts(pool,set()), ['coder','maker'])
        self.assertFalse(set(feeds.MEME_TRIAL_ACCOUNTS) & pool['discovered'])
        states={u:{'posts':[{'shortcode':u,'owner':u,'is_video':True}], 'has_more':False}
                for u in ['brainrot','coder','maker']}
        with patch('ig.feeds.STATE',states):
            result=feeds._pool_more(pool)
        self.assertEqual({p['owner'] for p in result['posts']},{'coder','maker'})

    def test_switching_feeds_discards_previous_discovery_pool(self):
        config.save_feeds({'memes':['brainrot'],'programming':['coder']})
        config.set_selected_feeds(['memes'])
        old=feeds.feed_pool(feeds.MIX)
        feeds.discover_into(old,['unrelated'])
        config.set_selected_feeds(['programming'])
        new=feeds.feed_pool(feeds.MIX)
        self.assertNotIn('unrelated',new['discovered'])
        self.assertNotIn('brainrot',feeds.available_accounts(new,set()))

    def test_candidate_cannot_enter_selected_topic_from_one_caption(self):
        state={'biography':'Chocolate company','posts':[{'is_video':True,'caption':'memes'}]}
        self.assertEqual(rec.matching_topics(state,['memes']),set())
        state['biography']='I make ironic comedy'
        self.assertEqual(rec.matching_topics(state,['memes']),{'memes'})
