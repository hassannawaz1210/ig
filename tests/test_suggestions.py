"""Instagram's suggested accounts as the main source of new creators."""
import time
from unittest.mock import patch

from ig import config, feeds, instagram, recommendations as rec, suggestions
from ig.reasons import Reason
from test_ig import ProjectTests


class SuggestionLeadTests(ProjectTests):
    def setUp(self):
        super().setUp()
        self.calls = []
        lists = {'creator_a': ['shared', 'only_a', 'creator_b'], 'creator_b': ['Shared', 'bad name!']}

        def fetch(source):
            self.calls.append(source)
            return lists.get(source, [])
        self.discovery = suggestions.SuggestionDiscovery(fetch=fetch)
        gap = patch.object(suggestions, 'REQUEST_GAP', 0)
        gap.start()
        self.addCleanup(gap.stop)

    def refresh(self, selected):
        self.discovery.leads(selected)
        if self.discovery._worker:
            self.discovery._worker.join(5)

    def test_offline_until_enabled(self):
        self.assertEqual(self.discovery.leads({'memes': ['creator_a']}), {})
        self.assertEqual(self.calls, [], 'tests and CLI commands never fetch suggestions')

    def test_suggestions_rank_by_how_many_creators_suggest_them(self):
        self.discovery.enabled = True
        selected = {'memes': ['creator_a', 'creator_b']}
        self.assertEqual(self.discovery.leads(selected), {}, 'nothing cached yet; loading never waits')
        self.discovery._worker.join(5)
        found = self.discovery.leads(selected, exclude={'creator_b'})
        self.assertEqual(set(found), {'shared', 'only_a'}, 'feed creators and invalid names are left out')
        self.assertEqual(found['shared']['count'], 2)
        self.assertEqual((found['only_a']['source'], found['only_a']['via'], found['only_a']['feed']),
                         ('suggested', 'creator_a', 'memes'))
        self.assertGreater(self.discovery.strength(found['shared']), self.discovery.strength(found['only_a']))
        self.assertGreater(self.discovery.strength(found['only_a']), 2.2, 'ahead of topic and caption leads')

    def test_cached_for_a_day_and_failures_wait(self):
        self.discovery.enabled = True
        self.refresh({'memes': ['creator_a']})
        self.refresh({'memes': ['creator_a']})
        self.assertEqual(self.calls, ['creator_a'])
        later = time.time() + suggestions.SUGGESTION_TTL + 1
        self.discovery.fetch = lambda source: (_ for _ in ()).throw(instagram.RateLimited('slow'))
        with patch.object(suggestions.time, 'time', return_value=later):
            self.refresh({'memes': ['creator_a']})
            self.assertIn('only_a', self.discovery.leads({'memes': ['creator_a']}), 'a failed refresh keeps the last list')
            self.calls.clear()
            self.discovery.fetch = lambda source: self.calls.append(source) or []
            self.refresh({'memes': ['creator_a']})
        self.assertEqual(self.calls, [], 'no retry until the failure delay passes')


class SuggestedTrialTests(ProjectTests):
    def setUp(self):
        super().setUp()
        config.save_feeds({'memes': ['saved_one']})
        info = {'source': 'suggested', 'via': 'saved_one', 'feed': 'memes', 'count': 1}
        self.leads = patch.object(feeds.SUGGESTIONS, 'leads', return_value={'suggested_one': dict(info)})
        self.leads.start()
        self.addCleanup(self.leads.stop)
        posts = lambda owner, caption: [{'shortcode': f'{owner[:6]}{i:05d}', 'owner': owner, 'is_video': True,
                                         'caption': caption} for i in range(6)]
        # A caption with no recognisable topic: membership comes from the suggestion.
        states = {'saved_one': {'posts': posts('saved_one', 'lol'), 'has_more': False},
                  'suggested_one': {'posts': posts('suggested_one', 'unrelated words'), 'has_more': False}}
        state = patch.object(feeds, 'STATE', states)
        state.start()
        self.addCleanup(state.stop)

    def batch(self, streak):
        pool = feeds.make_pool(['saved_one'])
        pool['saved_streak'] = streak
        return pool, feeds._pool_more(pool, limit=2, allow_network=False, snapshot=feeds.account_snapshot())

    def test_suggested_account_gets_the_next_turn_and_its_feed(self):
        with patch('ig.feeds.random.random', return_value=0.1):
            pool, batch = self.batch(suggestions.DISCOVERY_AFTER)
        self.assertEqual(batch['posts'][0]['owner'], 'suggested_one')
        self.assertEqual(batch['posts'][0]['discovery_source'], 'suggested')
        self.assertIn('suggested_one', pool['suggested_leads'])
        self.assertEqual(config.load_feeds(), {'memes': ['saved_one']}, 'trials never change your feeds')
        self.assertIn("Instagram suggests @suggested_one for people who like @saved_one",
                      ' '.join(batch['posts'][0]['reason']['details']))

    def test_a_suggested_account_may_supply_a_few_reels(self):
        for i in range(suggestions.SUGGESTED_REELS - 1):
            rec.record_view(f'old{i:05d}', 'suggested_one', f'event{i}')
        for i in range(3):  # a few of your own reels since then
            rec.record_view(f'saved{i:05d}', 'saved_one', f'saved{i}')
        with patch('ig.feeds.random.random', return_value=0.1):
            _, batch = self.batch(suggestions.DISCOVERY_AFTER)
        self.assertEqual(batch['posts'][0]['owner'], 'suggested_one', 'still allowed after two reels')
        rec.record_view('old99999', 'suggested_one', 'event9')
        for i in range(3, 6):
            rec.record_view(f'saved{i:05d}', 'saved_one', f'saved{i}')
        with patch('ig.feeds.random.random', return_value=0.1):
            _, batch = self.batch(suggestions.DISCOVERY_AFTER)
        self.assertNotEqual(batch['posts'][0]['owner'], 'suggested_one', 'rests after its allowance')


class SuggestedPreloadTests(ProjectTests):
    def test_refill_keeps_suggested_accounts_loaded(self):
        from ig.refill import Refill
        refill = Refill()
        self.addCleanup(refill.executor.shutdown, wait=True)
        refill.next_discovery_at = time.monotonic() + 60   # the shared discovery slot is busy
        pool = {'served': set(), 'suggested_leads': {'suggested_one'}}
        with patch('ig.refill.REELS.cached', return_value=None), \
             patch('ig.refill.REELS.load', return_value=({'posts': [], 'has_more': False}, True)):
            refill.schedule(pool, ['suggested_one'], {'saved_one'}, states={})
            self.assertIn('suggested_one', refill.pending, 'suggested accounts load outside the discovery slot')
            for future, _ in list(refill.pending.values()):
                future.result(timeout=2)
