"""Discovery from Instagram's public topic pages, without network access."""
import io
import json
import time
from unittest.mock import patch

from ig import backup, config, feeds, instagram, topics
from ig.reasons import Reason
from ig.server import Handler
from test_ig import ProjectTests
from test_profile_cache import TempDirs


def page(payload):
    """A topic page as Instagram embeds it: JSON in a data-sjs script tag."""
    return f'<html><script type="application/json" data-sjs>{json.dumps(payload)}</script></html>'


NARROW = page({'require': [{'data': {'xig_logged_out_popular_search_media_info': {'edges': [
    {'node': {'code': 'AAAAAAAAAAA', 'user': {'username': 'Quran_Daily', 'pk': '1'}}},
    {'node': {'code': 'BBBBBBBBBBB', 'user': {'username': 'deen.reminders', 'pk': '2'}}},
    {'node': {'code': 'CCCCCCCCCCC', 'user': {'username': 'quran_daily', 'pk': '1'}}},
    {'node': {'code': 'DDDDDDDDDDD', 'user': {'username': 'bad name!', 'pk': '3'}}},
]}}}]})
BROAD = page({'require': [{'data': {'ig_popular_search_category_hub': {'sections': [
    {'keyword_text': 'football skills', 'page_url': '/popular/football-skills/',
     'media_items': {'edges': [{'node': {'code': 'EEEEEEEEEEE', 'user': {'pk': '9'}}}]}},
    {'keyword_text': 'goalkeeper saves', 'page_url': '/popular/goalkeeper-saves/'},
]}}}]})


class TopicPageTests(ProjectTests):
    def test_topic_slugs(self):
        for word, slug in [('news_politics', 'news-politics'), ('News  politics', 'news-politics'),
                           ("Sheikh's talks", 'sheikhs-talks'), ('gym', 'gym'), ('  ', ''), ('a/b', 'ab')]:
            self.assertEqual(topics.topic_slug(word), slug, word)

    def test_narrow_page_lists_creators_once(self):
        creators, related = topics.parse_topic_page(NARROW)
        self.assertEqual(creators, {'quran_daily': 'AAAAAAAAAAA', 'deen.reminders': 'BBBBBBBBBBB'})
        self.assertEqual(related, [])

    def test_broad_page_lists_sub_topics(self):
        creators, related = topics.parse_topic_page(BROAD)
        self.assertEqual(creators, {}, 'broad pages carry ids only, not usernames')
        self.assertEqual(related, ['football-skills', 'goalkeeper-saves'])

    def test_unrecognised_page_finds_nothing(self):
        with self.assertRaises(instagram.UnexpectedResponse):
            topics.parse_topic_page('<html>login</html>')


class TopicWordTests(ProjectTests):
    def test_words_are_cleaned_and_stored_per_feed(self):
        config.save_feeds({'islam': ['a'], 'gym': []})
        self.assertEqual(config.set_feed_topic_words('islam', [' Quran ', 'quran', 'Hadith  of the day', '']),
                         ['quran', 'hadith of the day'])
        self.assertEqual(config.feed_topic_words(), {'islam': ['quran', 'hadith of the day'], 'gym': []})

    def test_invalid_words_are_refused_without_writing(self):
        config.save_feeds({'islam': []})
        config.set_feed_topic_words('islam', ['quran'])
        for words in ['quran', [1], ['<script>'], ['x' * 41], [f'w{i}' for i in range(21)]]:
            with self.assertRaises(ValueError, msg=repr(words)):
                config.set_feed_topic_words('islam', words)
        with self.assertRaises(KeyError):
            config.set_feed_topic_words('missing', ['quran'])
        self.assertEqual(config.feed_topic_words()['islam'], ['quran'])

    def test_deleted_feed_topics_return_when_restored(self):
        config.save_feeds({'islam': [], 'gym': []})
        config.set_feed_topic_words('islam', ['quran'])
        deletion = config.delete_feed('islam')
        self.assertNotIn('islam', config.feed_topic_words())
        config.restore_feed(deletion)
        self.assertEqual(config.feed_topic_words()['islam'], ['quran'])


class TopicDiscoveryTests(ProjectTests):
    def setUp(self):
        super().setUp()
        config.save_feeds({'islam': ['saved_one'], 'football': []})
        self.calls = []
        pages = {'islam': ({'quran_daily': 'A' * 11, 'saved_one': 'B' * 11}, []),
                 'quran': ({'deen.reminders': 'C' * 11}, []),
                 'football': ({}, ['football-skills']),
                 'football-skills': ({'skills.daily': 'D' * 11}, [])}

        def fetch(slug):
            self.calls.append(slug)
            return pages.get(slug, ({}, []))
        self.discovery = topics.TopicDiscovery(fetch=fetch)
        gap = patch.object(topics, 'REQUEST_GAP', 0)
        gap.start()
        self.addCleanup(gap.stop)

    def refresh(self, feed_names):
        self.discovery.leads(feed_names)
        worker = self.discovery._worker
        if worker:
            worker.join(5)

    def test_offline_until_enabled(self):
        self.assertEqual(self.discovery.leads(['islam']), {})
        self.assertEqual(self.calls, [], 'tests and CLI commands never fetch topic pages')

    def test_creators_come_from_feed_name_extra_words_and_sub_topics(self):
        config.set_feed_topic_words('islam', ['quran'])
        self.discovery.enabled = True
        self.assertEqual(self.discovery.leads(['islam', 'football']), {}, 'nothing cached yet; loading never waits')
        self.discovery._worker.join(5)
        self.refresh(['islam', 'football'])  # broad page fetched; now its sub-topic
        found = self.discovery.leads(['islam', 'football'], exclude={'saved_one'})
        self.assertEqual({name: {k: v for k, v in info.items() if k not in ('shortcode', 'username')}
                          for name, info in found.items()}, {
            'quran_daily': {'source': 'popular_topic', 'topic': 'islam', 'feed': 'islam'},
            'deen.reminders': {'source': 'popular_topic', 'topic': 'quran', 'feed': 'islam'},
            'skills.daily': {'source': 'popular_topic', 'topic': 'football skills', 'feed': 'football'}})
        self.assertEqual(sorted(self.calls), ['football', 'football-skills', 'islam', 'quran'])

    def test_pages_are_cached_between_batches(self):
        self.discovery.enabled = True
        self.refresh(['islam'])
        self.refresh(['islam'])
        self.assertEqual(self.calls, ['islam'], 'a topic page is fetched at most once per refresh period')
        with patch.object(topics.time, 'time', return_value=time.time() + topics.TOPIC_TTL + 1):
            self.refresh(['islam'])
        self.assertEqual(self.calls, ['islam', 'islam'])

    def test_failures_wait_and_keep_the_last_good_result(self):
        self.discovery.enabled = True
        self.refresh(['islam'])
        good = self.discovery.leads(['islam'])
        self.discovery.fetch = lambda slug: (_ for _ in ()).throw(instagram.RateLimited('slow down'))
        with patch.object(topics.time, 'time', return_value=time.time() + topics.TOPIC_TTL + 1):
            self.refresh(['islam'])
            self.assertEqual(self.discovery.leads(['islam']), good, 'a failed refresh keeps the cached creators')
            self.calls.clear()
            self.discovery.fetch = lambda slug: self.calls.append(slug) or ({}, [])
            self.refresh(['islam'])
        self.assertEqual(self.calls, [], 'no retry until the failure delay passes')

    def test_lead_reason_and_feed_membership(self):
        leads = {'collaborator': 2.5, 'credited': 2.0}
        reasons = {'collaborator': {'source': 'coauthor', 'via': 'x'}, 'credited': {'source': 'credit', 'via': 'x'}}
        snapshot = ({'saved_one'}, set(), set(), {'blocked_one'}, True)
        with patch.object(feeds.TOPICS, 'leads', return_value={
                'collaborator': {'source': 'popular_topic', 'topic': 'islam', 'feed': 'islam'},
                'credited': {'source': 'popular_topic', 'topic': 'islam', 'feed': 'islam'},
                'quran_daily': {'source': 'popular_topic', 'topic': 'quran', 'feed': 'islam'}}) as found:
            feeds.add_topic_leads(leads, reasons, {'islam': ['saved_one']}, snapshot)
        self.assertEqual(found.call_args.kwargs['exclude'], {'saved_one', 'blocked_one'})
        self.assertEqual(leads, {'collaborator': 2.5, 'credited': topics.TOPIC_LEAD, 'quran_daily': topics.TOPIC_LEAD})
        self.assertEqual(reasons['collaborator']['source'], 'coauthor', 'a collaborator keeps its stronger reason')
        self.assertEqual(reasons['credited']['source'], 'popular_topic', 'topic pages outrank reel-based credits')
        reason = Reason('quran_daily')
        reason.lead = reasons['quran_daily']
        self.assertIn('This reel appeared on Instagram’s “quran” page, a topic of your Islam feed.', ' '.join(reason.source_details()))


class TopicPoolRoomTests(ProjectTests):
    def test_topic_creators_fit_even_when_related_leads_fill_the_pool(self):
        pool = feeds.make_pool([])
        related = {f'related{i}': 1.0 for i in range(feeds.MAX_DISCOVERED + 50)}
        topic_names = ['topic_one', 'topic_two']
        leads = {**related, **{name: topics.TOPIC_LEAD for name in topic_names}}
        feeds.discover_leads(pool, leads, topic_names)
        self.assertEqual(len(pool['discovered'] - set(topic_names)), feeds.MAX_DISCOVERED)
        self.assertTrue(set(topic_names) <= pool['discovered'], 'topic creators are never crowded out')


class TopicRouteTests(ProjectTests):
    def request(self, data):
        handler = Handler.__new__(Handler)
        handler.path = '/feeds/topics'
        raw = json.dumps(data).encode()
        handler.headers = {'Content-Length': str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        with patch.object(handler, '_send') as send, patch.object(handler, '_allowed', return_value=True):
            handler.do_POST()
        status, body = send.call_args.args[:2]
        return status, json.loads(body)

    def test_save_validate_and_unknown_feed(self):
        config.save_feeds({'gym': []})
        self.assertEqual(self.request({'name': 'gym', 'topics': ['Powerlifting', 'calisthenics']}),
                         (200, {'name': 'gym', 'topics': ['powerlifting', 'calisthenics']}))
        status, body = self.request({'name': 'gym', 'topics': ['<b>']})
        self.assertEqual(status, 400)
        self.assertIn('not a topic', body['error'])
        self.assertEqual(self.request({'name': 'nope', 'topics': []})[0], 404)
        self.assertEqual(self.request({'topics': []})[0], 400)
        self.assertEqual(config.feed_topic_words(), {'gym': ['powerlifting', 'calisthenics']})


class TopicBackupTests(ProjectTests):
    def test_topics_round_trip_and_merge(self):
        config.save_feeds({'islam': ['a'], 'gym': []})
        config.set_feed_topic_words('islam', ['quran', 'hadith'])
        exported = backup.export()
        self.assertEqual(json.loads(exported)['feed_topics'], {'islam': ['quran', 'hadith']})
        config.set_feed_topic_words('islam', ['deen'])
        backup.merge(backup.parse(exported))
        self.assertEqual(config.feed_topic_words()['islam'], ['deen', 'quran', 'hadith'], 'existing topics come first')

    def test_older_backups_and_bad_topics(self):
        old = json.dumps({'format': 'ig-feeds', 'version': 1, 'feeds': {'gym': []}, 'blocked': []}).encode()
        self.assertEqual(backup.parse(old)['feed_topics'], {})
        for topics_value in [[], {'other': ['x']}, {'gym': ['<b>']}]:
            bad = json.dumps({'format': 'ig-feeds', 'version': 1, 'feeds': {'gym': []}, 'blocked': [],
                              'feed_topics': topics_value}).encode()
            with self.assertRaises(ValueError, msg=repr(topics_value)):
                backup.parse(bad)


class TopicSafetyTests(TempDirs):
    def setUp(self):
        super().setUp()
        patcher = patch('ig.history.HISTORY_JSON', config.CACHE / 'history.json')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_rotation_visits_every_subtopic(self):
        row = (0, {}, ['one', 'two', 'three', 'four', 'five'], None)
        seen = set()
        for cycle in range(5):
            with patch.object(topics.time, 'time', return_value=cycle * topics.TOPIC_TTL):
                chosen = topics.TopicDiscovery.related_topics(row)
                self.assertEqual(len(chosen), 2)
                seen.update(chosen)
        self.assertEqual(seen, set(row[2]))

    def test_login_shell_refresh_preserves_cached_creators(self):
        discovery = topics.TopicDiscovery(lambda slug: topics.parse_topic_page('<html>login</html>'))
        discovery._store('edits', {'creator': 'AAAAAAAAAAA'}, [])
        with patch.object(topics.time, 'time', return_value=time.time() + topics.TOPIC_TTL + 1), \
             patch.object(topics, 'REQUEST_GAP', 0):
            discovery._wanted = ['edits']
            discovery._run()
            row = discovery._rows(['edits'])['edits']
            self.assertEqual(row[1], {'creator': 'AAAAAAAAAAA'})
            self.assertFalse(discovery._due(row, time.time()))

    def test_recognised_empty_page_is_valid_but_null_payload_is_not(self):
        self.assertEqual(topics.parse_topic_page(page({'xig_logged_out_popular_search_media_info': {'edges': []}})), ({}, []))
        with self.assertRaises(instagram.UnexpectedResponse):
            topics.parse_topic_page(page({'xig_logged_out_popular_search_media_info': None}))

    def test_topic_reel_owner_must_match(self):
        discovery = topics.TopicDiscovery()
        info = {'username': 'creator', 'shortcode': 'AAAAAAAAAAA'}
        with patch.object(feeds.REELS, 'media', return_value={'is_video': True, 'owner': 'someone_else'}):
            with self.assertRaises(instagram.UnexpectedResponse):
                discovery.trial_state(info, network=True)
        self.assertIsNone(discovery.trial_state(info))

    def test_empty_feed_trials_exact_topic_reel_without_profile_requests(self):
        from ig.refill import Refill
        config.save_feeds({'edits': []})
        discovery = topics.TopicDiscovery()
        discovery._store('edits', {'creator': 'AAAAAAAAAAA'}, [])
        refill = Refill()
        self.addCleanup(refill.executor.shutdown, wait=True)
        post = {'shortcode': 'AAAAAAAAAAA', 'owner': 'creator', 'is_video': True, 'caption': 'film edit'}
        with patch.object(feeds, 'TOPICS', discovery), patch('ig.refill.TOPICS', discovery), \
             patch.object(feeds, 'REFILL', refill), \
             patch.object(feeds, 'STATE', {'creator': {'posts': [{'shortcode': 'UNRELATED01', 'owner': 'creator', 'is_video': True}], 'has_more': True}}), \
             patch.object(feeds.REELS, 'media', return_value=post) as media, \
             patch.object(feeds.REELS, 'load') as profile:
            pool = feeds.make_pool([])
            first = feeds.pool_more(pool)
            for future, _ in list(refill.pending.values()):
                future.result(timeout=2)
            result = first if first['posts'] else feeds.pool_more(pool)
            self.assertEqual([p['shortcode'] for p in result['posts']], ['AAAAAAAAAAA'])
            self.assertNotIn('creator', pool.get('accepted', set()))
            self.assertEqual(feeds.pool_more(pool)['posts'], [])
            media.assert_called_once_with('AAAAAAAAAAA')
            profile.assert_not_called()

    def test_empty_feed_waits_for_background_topic_page(self):
        config.save_feeds({'edits': []})
        with patch.object(feeds.TOPICS, 'leads', return_value={}), \
             patch.object(feeds.TOPICS, 'waiting', return_value=True):
            result = feeds.pool_more(feeds.make_pool([]))
        self.assertEqual(result['posts'], [])
        self.assertTrue(result['has_more'])

    def test_empty_feed_does_not_wait_thirty_seconds_between_topic_reels(self):
        from ig.refill import Refill
        discovery = topics.TopicDiscovery()
        refill = Refill()
        self.addCleanup(refill.executor.shutdown, wait=True)
        refill.next_discovery_at = time.monotonic() + 30
        info = {'username': 'creator', 'shortcode': 'AAAAAAAAAAA'}
        pool = {'served': set(), 'topic_trials': {'creator': info}}
        with patch('ig.refill.TOPICS', discovery), patch.object(feeds.REELS, 'media', return_value={
                'shortcode': 'AAAAAAAAAAA', 'owner': 'creator', 'is_video': True}) as media:
            refill.schedule(pool, ['creator'], set(), states={})
            self.assertIn('creator', refill.pending)
            for future, _ in list(refill.pending.values()):
                future.result(timeout=2)
            media.assert_called_once()

    def test_consumed_topic_reels_are_not_refetched_after_cache_expiry(self):
        from ig.refill import Refill
        refill = Refill()
        self.addCleanup(refill.executor.shutdown, wait=True)
        pool = {'served': {'AAAAAAAAAAA'}, 'topic_trials': {
            'creator': {'username': 'creator', 'shortcode': 'AAAAAAAAAAA'}}}
        with patch('ig.refill.TOPICS.trial_state') as load:
            refill.schedule(pool, ['creator'], set(), states={})
            load.assert_not_called()
        self.assertFalse(refill.pending)
