"""Trending topics, topic search, feed topic suggestions and topic pages, offline."""
import io
import json
import time
from pathlib import Path
from unittest.mock import patch

from ig import config, instagram, recommendations, server, topics, views
from ig.server import Handler
from test_ig import ProjectTests

FIXTURES = Path(__file__).with_name('fixtures')


def page(payload, title=''):
    """An Instagram page: JSON in a data-sjs script tag, optionally with a <title>."""
    head = f'<title>{title}</title>' if title else ''
    return f'<html><head>{head}</head><script type="application/json" data-sjs>{json.dumps(payload)}</script></html>'


def explore_page():
    """The synthetic /explore/ fixture, nested the way the real page embeds it."""
    data = json.loads((FIXTURES / 'explore_sections.json').read_text())
    sections = {'popular_search_home_sections': data['popular_search_home_sections']}
    return page({'require': [['ScheduledServerJS', 'handle', None, [{'__bbox': {'require': [
        ['RelayPrefetchedStreamCache', 'next', [], ['adp_PolarisPopularSearchHomeQuery', {'__bbox': {
            'complete': True, 'result': {'data': sections}}}]]]}}]]]})


def narrow_page():
    return page({'require': [{'data': {'xig_logged_out_popular_search_media_info': {'edges': [
        {'node': {'__typename': 'XIGPolarisVideoMedia', 'code': 'AAAAAAAAAAA', 'play_count': 621219,
                  'user': {'username': 'Hunter_WK', 'pk': '1'}, 'caption': {'text': 'Walking on air #calisthenics'},
                  'display_uri': 'https://scontent.cdninstagram.com/v/a.jpg', 'video_versions': [{'url': 'x'}]}},
        {'node': {'__typename': 'XIGPolarisImageMedia', 'code': 'BBBBBBBBBBB',
                  'user': {'username': 'bad name!'}, 'display_uri': 'http://insecure.test/b.jpg', 'like_count': 7}},
        {'node': {'__typename': 'XIGPolarisVideoMedia', 'code': 'AAAAAAAAAAA', 'user': {'username': 'dupe'}}},
        {'node': {'__typename': 'XIGPolarisVideoMedia', 'code': 'bad code!'}},
    ]}, 'related': [{'page_url': '/popular/planche/'}]}}]},
        title='Calisthenics &bull; 79M reels on Instagram'.replace('&bull;', '•'))


def broad_page():
    return page({'require': [{'data': {'ig_popular_search_category_hub': {'sections': [
        {'keyword_text': 'cute animal doodles', 'page_url': '/popular/cute-animal-doodles/',
         'media_items': {'edges': [{'node': {'__typename': 'XIGPolarisVideoMedia', 'code': 'CCCCCCCCCCC',
                                             'user': {'pk': '9'}, 'play_count': 10}}]}},
    ]}}}]}, title='Cute animals on Instagram')


def empty_topic_page():
    return page({'require': [{'data': {'xig_logged_out_popular_search_media_info': {'edges': []}}}]},
                title='Quiet on Instagram')


TRENDING = {'sections': [
    {'title': 'Trending now', 'topics': [topics.topic_item('dana white', count=3215313),
                                         topics.topic_item('diwali', count=113870191)]},
    {'title': 'Learn something new', 'topics': [topics.topic_item('edit', count=454501433),
                                                topics.topic_item('editing tips'),
                                                topics.topic_item('makeup tutorial')]},
    {'title': 'Get inspired', 'topics': [topics.topic_item('gym'), topics.topic_item('art')]},
], 'creators': [{'username': 'example', 'full_name': 'Example', 'followers_label': '1K followers',
                 'profile_pic_url': None}]}


class ExplorePageTests(ProjectTests):
    def setUp(self):
        super().setUp()
        self.parsed = topics.parse_explore_page(explore_page())

    def test_sections_keep_their_titles_and_order(self):
        self.assertEqual([section['title'] for section in self.parsed['sections']],
                         ['Trending now', 'Pop culture moments', "Find what you're into", 'Learn something new'],
                         'a section of creators only, and one with no usable topics, are not topic sections')

    def test_topics_have_name_slug_word_count_and_safe_image(self):
        trending = self.parsed['sections'][0]['topics']
        self.assertEqual(trending[0], {
            'name': 'Dana White', 'slug': 'dana-white', 'word': 'dana white', 'count_label': '3M posts',
            'image': 'https://scontent.cdninstagram.com/v/t51.0-15/trend1.jpg?stp=dst-jpg'})
        self.assertEqual([(t['slug'], t['count_label'], t['image']) for t in trending[1:]], [
            ('festivaloflights', '18 posts', None),   # http image refused
            ('diwali', '114M posts', None),           # not Instagram's CDN
            ('skims', '2M posts', None),
        ], 'entries without a keyword, unsavable words and repeats are left out')
        for section in self.parsed['sections']:
            for item in section['topics']:
                self.assertEqual(topics.topic_slug(item['word']), item['slug'])
                config.clean_topic_words([item['word']])  # every word can be saved as a feed topic

    def test_page_slug_wins_over_the_keyword(self):
        culture = {t['slug']: t for t in self.parsed['sections'][1]['topics']}
        self.assertEqual(culture['edc-orlando-2026']['word'], 'edc orlando 2026')
        self.assertEqual(culture['edc-orlando-2026']['name'], 'Edc Orlando')
        self.assertEqual(culture['ronaldo-retirement']['count_label'], '1K posts')
        self.assertTrue(culture['ronaldo-retirement']['image'].startswith('https://instagram.fxyz1-1.fna.fbcdn.net/'))
        self.assertIsNone(culture['jetcrash']['count_label'])

    def test_browse_categories_use_their_display_name(self):
        categories = self.parsed['sections'][2]['topics']
        self.assertEqual([(t['name'], t['slug'], t['word'], t['count_label']) for t in categories],
                         [('Cute animals', 'cute-animals', 'cute animals', None),
                          ('Motorsports and Cycling', 'cycling', 'cycling', None)])

    def test_creators_are_validated(self):
        self.assertEqual(self.parsed['creators'], [
            {'username': 'example.creator', 'full_name': 'Example Creator', 'followers_label': '13M followers',
             'profile_pic_url': 'https://scontent.cdninstagram.com/v/t51.0-19/creator.jpg'},
            {'username': 'second_creator', 'full_name': '', 'followers_label': '566K followers',
             'profile_pic_url': None},
            {'username': 'no_count', 'full_name': '', 'followers_label': None, 'profile_pic_url': None},
        ])

    def test_pages_without_the_directory_are_refused(self):
        for html in ('<html>Log in</html>', page({'popular_search_home_sections': None}),
                     page({'popular_search_home_sections': [{'display_title': 'Empty',
                                                             'entries_connection': {'edges': []}}]})):
            with self.assertRaises(instagram.UnexpectedResponse):
                topics.parse_explore_page(html)

    def test_count_labels_match_instagram(self):
        for count, label in [(0, '0 posts'), (1, '1 post'), (18, '18 posts'), (999, '999 posts'),
                             (1000, '1K posts'), (1175, '1K posts'), (3596, '4K posts'), (348565, '349K posts'),
                             (999_600, '1M posts'), (113_870_191, '114M posts'),
                             (1_121_098_974, '1B posts'), (8_551_119_005, '9B posts')]:
            self.assertEqual(topics.count_label(count, 'post', 'posts'), label, count)
        for bad in (None, -1, True, '12', 1.5):
            self.assertIsNone(topics.count_label(bad, 'post', 'posts'))

    def test_only_instagram_cdn_images(self):
        good = ['https://scontent.cdninstagram.com/a.jpg', 'https://scontent-lhr8-1.cdninstagram.com/a.jpg',
                'https://instagram.fisb1-2.fna.fbcdn.net/a.jpg']
        bad = ['http://scontent.cdninstagram.com/a.jpg', 'https://cdninstagram.com.evil.test/a.jpg',
               'https://evilcdninstagram.com/a.jpg', 'javascript:alert(1)', 'https://user@x.cdninstagram.com/a',
               'https://[bad/a.jpg', None, 5]
        for url in good:
            self.assertEqual(topics.instagram_image(url), url)
        for url in bad:
            self.assertIsNone(topics.instagram_image(url), url)

    def test_topic_item_words_always_make_their_slug(self):
        self.assertEqual(topics.topic_item('  News   Politics '),
                         {'name': 'News Politics', 'slug': 'news-politics', 'word': 'news politics',
                          'count_label': None, 'image': None})
        self.assertIsNone(topics.topic_item(''))
        self.assertIsNone(topics.topic_item('x' * 41), 'longer than a feed topic may be')
        self.assertIsNone(topics.topic_item('<b>'))


class TopicReelsTests(ProjectTests):
    def test_narrow_page_reels_name_and_count(self):
        result = topics.parse_topic_reels(narrow_page())
        self.assertEqual((result['name'], result['count_label']), ('Calisthenics', '79M reels'))
        self.assertEqual(result['posts'], [
            {'shortcode': 'AAAAAAAAAAA', 'owner': 'hunter_wk', 'is_video': True, 'is_carousel': False,
             'url': 'https://www.instagram.com/reel/AAAAAAAAAAA/',
             'image': 'https://scontent.cdninstagram.com/v/a.jpg', 'like_count': None, 'play_count': 621219,
             'comment_count': None, 'taken_at': None, 'caption': 'Walking on air #calisthenics'},
            {'shortcode': 'BBBBBBBBBBB', 'owner': None, 'is_video': False, 'is_carousel': False,
             'url': 'https://www.instagram.com/p/BBBBBBBBBBB/', 'image': '', 'like_count': 7, 'play_count': None,
             'comment_count': None, 'taken_at': None, 'caption': None},
        ])
        self.assertEqual(result['creators'], {'hunter_wk': 'AAAAAAAAAAA', 'dupe': 'AAAAAAAAAAA'})
        self.assertEqual(result['related'], ['planche'])

    def test_broad_page_reels_have_no_owner(self):
        result = topics.parse_topic_reels(broad_page())
        self.assertEqual((result['name'], result['count_label']), ('Cute animals', None))
        self.assertEqual([(p['shortcode'], p['owner'], p['play_count']) for p in result['posts']],
                         [('CCCCCCCCCCC', None, 10)])
        self.assertEqual(result['related'], ['cute-animal-doodles'])

    def test_unrecognised_page_is_an_error(self):
        with self.assertRaises(instagram.UnexpectedResponse):
            topics.parse_topic_reels('<html><title>Log in • Instagram</title></html>')

    def test_missing_topic_is_empty(self):
        class Response:
            status_code, text = 404, ''
        with patch.object(instagram, 'request', return_value=Response()):
            self.assertEqual(topics.fetch_topic_reels('nope')['posts'], [])
        Response.status_code = 500
        with patch.object(instagram, 'request', return_value=Response()), \
                self.assertRaises(instagram.UnexpectedResponse):
            topics.fetch_topic_reels('nope')


class FakeDiscovery(topics.TopicDiscovery):
    """Topic discovery with fixed cached rows and no worker."""

    def __init__(self, rows=None):
        super().__init__(fetch=lambda slug: self.fail(slug))
        self.rows = rows or {}
        self.requested = []

    @staticmethod
    def fail(slug):
        raise AssertionError(f'fetched {slug}')

    def _rows(self, slugs):
        return {slug: self.rows[slug] for slug in slugs if slug in self.rows}

    def known_slugs(self):
        found = {}
        for slug, row in self.rows.items():
            found[slug] = None
            found.update(dict.fromkeys(row[2]))
        return list(found)

    def request_pages(self, slugs):
        self.requested.extend(slugs)


def row(*related, fetched=None):
    return (time.time() if fetched is None else fetched, {}, list(related), None)


class DirectoryTests(ProjectTests):
    def setUp(self):
        super().setUp()
        self.calls = 0
        self.result = TRENDING

        def fetch():
            self.calls += 1
            if isinstance(self.result, Exception):
                raise self.result
            return self.result
        self.fetch = fetch

    def directory(self, rows=None):
        return topics.TopicDirectory(FakeDiscovery(rows), fetch=self.fetch,
                                     fetch_page=lambda slug: self.fail(f'fetched page {slug}'))


class TrendingTests(DirectoryTests):
    def test_fetched_once_then_cached_in_memory_and_on_disk(self):
        directory = self.directory()
        first = directory.trending()
        self.assertEqual(first['sections'], TRENDING['sections'])
        self.assertIsInstance(first['fetched_at'], int)
        self.assertEqual(directory.trending(), first)
        self.assertEqual(self.calls, 1)
        restarted = self.directory()
        self.assertEqual(restarted.trending()['creators'], TRENDING['creators'])
        self.assertEqual(self.calls, 1, 'a restart reuses the saved copy')

    def test_failure_serves_the_last_good_copy(self):
        directory = self.directory()
        good = directory.trending()
        directory._data = {**good, 'fetched_at': good['fetched_at'] - topics.TRENDING_TTL - 1}
        self.result = instagram.RateLimited('Instagram is rate limiting requests; wait a few minutes')
        with self.assertLogs('ig.topics', 'WARNING'):
            self.assertEqual(directory.trending()['sections'], TRENDING['sections'])
        self.assertEqual(self.calls, 2)
        directory.trending()
        self.assertEqual(self.calls, 2, 'no retry straight after a failure')

    def test_failure_without_a_copy_raises_and_waits_before_retrying(self):
        directory = self.directory()
        self.result = instagram.UnexpectedResponse('Instagram did not return its trending topics')
        for _ in range(2):
            with self.assertRaises(instagram.UnexpectedResponse):
                directory.trending()
        self.assertEqual(self.calls, 1)
        directory._failed_at -= topics.TRENDING_RETRY
        self.result = TRENDING
        self.assertEqual(directory.trending()['sections'], TRENDING['sections'])

    def test_unreadable_saved_copy_is_ignored(self):
        with recommendations.database() as connection:
            topics._directory_schema(connection)
            connection.execute("INSERT INTO trending_topics VALUES (1, ?, 'not json')", (time.time(),))
        with self.assertLogs('ig.topics', 'WARNING'):
            self.assertIsNone(self.directory().cached())

    def test_background_refresh_stays_offline_in_tests(self):
        directory = self.directory()
        directory.refresh_in_background()
        self.assertIsNone(directory._refresher)
        self.assertEqual(self.calls, 0)


class SearchTests(DirectoryTests):
    def setUp(self):
        super().setUp()
        self.directory_ = self.directory({'gym': row('gym-motivation', 'home-workout-edit'),
                                          'quran': row('quran-recitation')})
        self.directory_.trending()
        self.calls = 0

    def search(self, text):
        return [item['slug'] for item in self.directory_.search(text)]

    def test_prefix_matches_first_then_word_starts_then_anywhere(self):
        self.assertEqual(self.search('edit'), ['edit', 'editing-tips', 'home-workout-edit'])
        self.assertEqual(self.search('GYM'), ['gym', 'gym-motivation'])
        self.assertEqual(self.search('tut'), ['makeup-tutorial'])
        self.assertEqual(self.search('ali'), ['diwali'])

    def test_multi_word_and_slug_queries(self):
        self.assertEqual(self.search('makeup  tu'), ['makeup-tutorial'])
        self.assertEqual(self.search('quran-rec'), ['quran-recitation'])

    def test_slug_only_topics_get_readable_names_and_no_count(self):
        found = self.directory_.search('recitation')
        self.assertEqual(found, [{'name': 'Quran Recitation', 'slug': 'quran-recitation', 'word': 'quran recitation',
                                  'count_label': None, 'image': None}])
        self.assertEqual(self.directory_.search('dana')[0]['count_label'], '3M posts')

    def test_limit_and_empty_query(self):
        rows = {f'topic-{i}': row() for i in range(30)}
        directory = self.directory(rows)
        self.assertEqual(len(directory.search('topic')), topics.SEARCH_LIMIT)
        self.assertEqual(directory.search('   '), [])
        self.assertEqual(directory.search('zzz'), [])
        self.assertEqual(self.calls, 0, 'searching never fetches')


class SuggestionTests(DirectoryTests):
    def setUp(self):
        super().setUp()
        config.save_feeds({'gym': ['lifter_one', 'lifter_two', 'lifter_three'], 'quiet': []})
        config.set_feed_topic_words('gym', ['calisthenics', 'never fetched'])
        self.rows = {'gym': row('gym-motivation', 'calisthenics', 'powerlifting'),
                     'calisthenics': row('planche', 'gym-motivation'),
                     'never-fetched': (0, {}, [], time.time())}  # only ever failed
        recommendations.remember_reels([
            {'shortcode': 'A' * 11, 'owner': 'lifter_one', 'caption': '#Deadlift #fyp #gym #homeworkout'},
            {'shortcode': 'B' * 11, 'owner': 'lifter_two', 'caption': '#deadlift #fyp'},
            {'shortcode': 'C' * 11, 'owner': 'lifter_one', 'caption': '#legday #makeuptutorial'},
            {'shortcode': 'D' * 11, 'owner': 'lifter_one', 'caption': '#makeuptutorial'},
            {'shortcode': 'E' * 11, 'owner': 'stranger', 'caption': '#cooking #cooking2 #cooking'},
            {'shortcode': 'F' * 11, 'owner': 'stranger2', 'caption': '#cooking'},
        ])

    def test_related_then_hashtags(self):
        directory = self.directory(self.rows)
        directory.trending()
        suggestions = directory.suggest('gym')
        self.assertEqual([(s['slug'], s['reason']) for s in suggestions], [
            ('gym-motivation', 'related'), ('powerlifting', 'related'), ('planche', 'related'),
            ('deadlift', 'hashtag'),            # two of the feed's creators use it
            ('makeup-tutorial', 'hashtag'),     # one creator, but a known topic
        ], 'no general trending filler: it rarely fits a feed')
        self.assertNotIn('legday', [s['slug'] for s in suggestions], 'one creator and not a known topic')
        self.assertEqual(directory.discovery.requested, ['never-fetched'])
        for item in suggestions:
            self.assertEqual(set(item), {'name', 'slug', 'word', 'count_label', 'image', 'reason'})
            self.assertEqual(topics.topic_slug(item['word']), item['slug'])

    def test_existing_topics_and_common_tags_are_left_out(self):
        directory = self.directory(self.rows)
        slugs = [s['slug'] for s in directory.suggest('gym')]
        for own in ('gym', 'calisthenics', 'never-fetched'):
            self.assertNotIn(own, slugs)
        self.assertNotIn('fyp', slugs)
        self.assertNotIn('cooking', slugs, 'hashtags of other feeds do not count')

    def test_a_feed_with_little_data_gets_no_filler(self):
        self.assertEqual(self.directory().suggest('quiet'), [], 'trending topics are on the strip, not suggested here')

    def test_sponsorship_and_tiny_tags_are_not_topics(self):
        config.save_feeds({'gym': ['lifter_one', 'lifter_two', 'lifter_three'], 'quiet': ['brand_one', 'brand_two']})
        recommendations.remember_reels([
            {'shortcode': f'Q{i:010d}', 'owner': owner, 'caption': '#ad #sponsored #ok #squats'}
            for i, owner in enumerate(['brand_one', 'brand_two', 'brand_one', 'brand_two'])])
        self.assertEqual([s['slug'] for s in self.directory(self.rows).suggest('quiet')], ['squats'])

    def test_trending_failure_still_suggests_the_rest(self):
        self.result = instagram.NetworkError('Could not reach Instagram; check your connection')
        directory = self.directory({'quiet': row('silence')})
        self.assertEqual([(s['slug'], s['reason']) for s in directory.suggest('quiet')], [('silence', 'related')])

    def test_at_most_twelve(self):
        rows = {'gym': row(*[f'related-{i}' for i in range(20)])}
        self.assertEqual(len(self.directory(rows).suggest('gym')), topics.MAX_SUGGESTIONS)

    def test_unknown_feed(self):
        with self.assertRaises(KeyError):
            self.directory().suggest('missing')


class DiscoveryCacheTests(ProjectTests):
    def test_known_slugs_and_request_pages(self):
        discovery = topics.TopicDiscovery(fetch=lambda slug: ({}, []))
        discovery._store('gym', {'a': 'A' * 11}, ['gym-motivation', 'Not A Slug'])
        discovery._store('art', {}, [])
        self.assertEqual(discovery.known_slugs(), ['art', 'gym', 'gym-motivation'])
        discovery.request_pages(['new-topic'])
        self.assertIsNone(discovery._worker, 'offline until the server enables it')
        discovery.enabled = True
        with patch.object(discovery, '_run'):
            discovery._wanted = ['queued']
            discovery.request_pages(['gym', 'new-topic'])
        self.assertEqual(discovery._wanted, ['queued', 'new-topic'], 'cached pages are not refetched')


class RouteTests(DirectoryTests):
    def setUp(self):
        super().setUp()
        self.pages = {'calisthenics': topics.parse_topic_reels(narrow_page()),
                      'quiet': topics.parse_topic_reels(empty_topic_page())}
        self.page_calls = []

        def fetch_page(slug):
            self.page_calls.append(slug)
            if slug not in self.pages:
                raise instagram.RateLimited('Instagram is rate limiting requests; wait a few minutes')
            return self.pages[slug]
        self.dir = topics.TopicDirectory(FakeDiscovery({'gym': row('powerlifting')}), fetch=self.fetch,
                                         fetch_page=fetch_page)
        replace = patch.object(server, 'DIRECTORY', self.dir)
        replace.start()
        self.addCleanup(replace.stop)

    def get(self, path):
        handler = Handler.__new__(Handler)
        handler.path = path
        with patch.object(handler, '_send') as send, patch.object(handler, '_allowed', return_value=True):
            handler.do_GET()
        status, body, kind = send.call_args.args[:3]
        return status, (json.loads(body) if kind == 'application/json' else body.decode())

    def post(self, path, data):
        handler = Handler.__new__(Handler)
        handler.path = path
        raw = json.dumps(data).encode()
        handler.headers = {'Content-Length': str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        with patch.object(handler, '_send') as send, patch.object(handler, '_allowed', return_value=True), \
                patch.object(handler, '_media', return_value={'shortcode': data['code'], 'video': 'v'}) as media:
            handler.do_POST()
        return send.call_args.args[0], media

    def test_trending(self):
        status, body = self.get('/topics/trending')
        self.assertEqual(status, 200)
        self.assertEqual(set(body), {'sections', 'creators', 'fetched_at'})
        self.assertEqual(body['sections'][0]['topics'][0]['slug'], 'dana-white')

    def test_trending_failure_is_a_safe_json_error(self):
        self.result = instagram.RateLimited('Instagram is rate limiting requests; wait a few minutes')
        self.assertEqual(self.get('/topics/trending'),
                         (429, {'error': 'Instagram is rate limiting requests; wait a few minutes'}))
        self.dir._failed_at = 0
        self.result = RuntimeError('database password is hunter2')
        with self.assertLogs('ig', 'ERROR'):
            status, body = self.get('/topics/trending')
        self.assertEqual(status, 500)
        self.assertNotIn('hunter2', body['error'])

    def test_search(self):
        self.dir.trending()
        status, body = self.get('/topics/search?q=Pow')
        self.assertEqual((status, [t['slug'] for t in body['topics']]), (200, ['powerlifting']))
        self.assertEqual(self.get('/topics/search?q=dana%20wh')[1]['topics'][0]['name'], 'Dana White')
        self.assertEqual(self.get('/topics/search'), (200, {'topics': []}))
        self.assertEqual(self.get('/topics/search?q=' + 'a' * 101)[0], 400)

    def test_suggest(self):
        config.save_feeds({'gym': []})
        status, body = self.get('/feeds/topics/suggest?feed=gym')
        self.assertEqual(status, 200)
        self.assertEqual(body['suggestions'][0]['slug'], 'powerlifting')
        self.assertEqual(body['suggestions'][0]['reason'], 'related')
        self.assertEqual(self.get('/feeds/topics/suggest?feed=missing'), (404, {'error': 'Feed not found'}))
        self.assertEqual(self.get('/feeds/topics/suggest')[0], 400)
        self.assertEqual(self.get('/feeds/topics/suggest?feed=Bad%20Name')[0], 400)

    def test_topic_page(self):
        self.dir.trending()
        self.pages['edit'] = self.pages['calisthenics']
        status, html = self.get('/topic/edit')
        self.assertEqual(status, 200)
        self.assertIn('<title>Edit', html)
        self.assertIn('455M posts', html, 'the known count from the trending list')
        self.assertIn('https://www.instagram.com/popular/edit/', html)
        self.assertIn('AAAAAAAAAAA', html)
        status, html = self.get('/topic/calisthenics')
        self.assertIn('79M reels', html, 'the count from the topic page itself')
        self.get('/topic/calisthenics')
        self.assertEqual(self.page_calls, ['edit', 'calisthenics'], 'opened pages are reused for a while')

    def test_topic_page_stores_its_creators_for_discovery(self):
        with patch.object(self.dir.discovery, '_store') as store:
            self.get('/topic/calisthenics')
        store.assert_called_once_with('calisthenics', {'hunter_wk': 'AAAAAAAAAAA', 'dupe': 'AAAAAAAAAAA'},
                                      ['planche'])

    def test_empty_and_failed_topic_pages(self):
        status, html = self.get('/topic/quiet')
        self.assertEqual(status, 200)
        self.assertIn('Instagram shows no popular reels for this topic', html)
        self.assertIn('Popular on Instagram', html)
        status, html = self.get('/topic/limited')
        self.assertEqual(status, 429)
        self.assertIn('rate limiting', html)

    def test_invalid_topic_slugs(self):
        for path in ('/topic/Edit', '/topic/a_b', '/topic/%3Cscript%3E', '/topic/-edit', '/topic/' + 'a' * 61):
            status, html = self.get(path)
            self.assertEqual(status, 400, path)
            self.assertNotIn('<script>', html)
        self.assertEqual(self.page_calls, [])

    def test_topic_media_lookups(self):
        status, media = self.post('/topic/calisthenics/media', {'code': 'AAAAAAAAAAA'})
        self.assertEqual(status, 200)
        media.assert_called_once_with('AAAAAAAAAAA')
        status, media = self.post('/topic/Bad_Slug/media', {'code': 'AAAAAAAAAAA'})
        self.assertEqual(status, 404)
        media.assert_not_called()


class TopicHeaderTests(ProjectTests):
    def test_header_escapes_and_links_to_instagram(self):
        html = views.viewer_html('<b>', 'Popular on Instagram', [], paged=False, empty_message='None yet',
                                 topic={'name': '<b>Edit</b>', 'slug': 'edit', 'count_label': '455M posts'})
        self.assertIn('&lt;b&gt;Edit&lt;/b&gt;', html)
        self.assertNotIn('<b>Edit', html)
        self.assertIn('455M posts on Instagram', html)
        self.assertIn('href="https://www.instagram.com/popular/edit/"', html)
        self.assertIn('None yet', html)

    def test_header_without_count(self):
        html = views.viewer_html('Quiet', 'Popular on Instagram', [], paged=False,
                                 topic={'name': 'Quiet', 'slug': 'quiet', 'count_label': None})
        self.assertNotIn('sound-count', html)


class TopicPagingTests(DirectoryTests):
    """More reels for an open topic page, through the cursor the page gives."""

    def reel(self, code):
        return {'shortcode': code, 'owner': 'someone', 'is_video': True, 'is_carousel': False,
                'url': f'https://www.instagram.com/reel/{code}/', 'image': '', 'like_count': None,
                'play_count': 5, 'comment_count': None, 'taken_at': None, 'caption': None}

    def make(self, pages):
        first = {'name': 'Calisthenics', 'count_label': '79M posts', 'posts': [self.reel('A' * 11)],
                 'creators': {}, 'related': [], 'cursor': 'c1', 'has_more': True, 'context': {'session': object(), 'lsd': 'x'}}
        calls = []

        def more(slug, context, cursor):
            calls.append(cursor)
            return pages.pop(0)
        directory = topics.TopicDirectory(FakeDiscovery(), fetch=self.fetch, fetch_page=lambda slug: first, fetch_more=more)
        return directory, calls

    def test_pages_follow_the_cursor_and_skip_repeats(self):
        directory, calls = self.make([
            {'posts': [self.reel('A' * 11), self.reel('B' * 11)], 'cursor': 'c2', 'has_more': True},
            {'posts': [self.reel('C' * 11)], 'cursor': None, 'has_more': False}])
        directory.topic_page('calisthenics')
        self.assertEqual([p['shortcode'] for p in directory.topic_more('calisthenics')['posts']], ['B' * 11], 'no repeats')
        last = directory.topic_more('calisthenics')
        self.assertEqual(([p['shortcode'] for p in last['posts']], last['has_more']), (['C' * 11], False))
        self.assertEqual(directory.topic_more('calisthenics'), {'posts': [], 'has_more': False}, 'no request after the end')
        self.assertEqual(calls, ['c1', 'c2'])

    def test_a_page_of_only_repeats_ends_the_list_and_reopening_starts_over(self):
        directory, calls = self.make([{'posts': [self.reel('A' * 11)], 'cursor': 'c2', 'has_more': True},
                                      {'posts': [self.reel('D' * 11)], 'cursor': 'c3', 'has_more': True}])
        directory.topic_page('calisthenics')
        self.assertEqual(directory.topic_more('calisthenics'), {'posts': [], 'has_more': False})
        directory.topic_page('calisthenics')   # reopened (from the cache): paging starts again
        self.assertEqual([p['shortcode'] for p in directory.topic_more('calisthenics')['posts']], ['D' * 11])
        self.assertEqual(calls, ['c1', 'c1'])

    def test_unopened_topic_and_failures(self):
        directory, _ = self.make([])
        self.assertEqual(directory.topic_more('never-opened'), {'posts': [], 'has_more': False})
        with self.assertRaises(instagram.UnexpectedResponse):
            topics.fetch_topic_more('calisthenics', {}, 'c1')

    def test_next_page_cursor_from_the_page_data(self):
        payload = {'data': {'xig_logged_out_popular_search_media_info': {
            'edges': [], 'page_info': {'end_cursor': 'abc', 'has_next_page': True}}}}
        self.assertEqual(topics._next_page(payload), ('abc', True))
        payload['data']['xig_logged_out_popular_search_media_info']['page_info']['has_next_page'] = False
        self.assertEqual(topics._next_page(payload), ('abc', False))
        self.assertEqual(topics._next_page({'data': {}}), (None, False))
