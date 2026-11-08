"""New creators from Instagram's public topic pages (instagram.com/popular/<topic>/).

Leads from captions, tags and comments only ever reach creators connected to
the ones already in your feeds. Topic pages list popular reels for a topic
from anyone, so they reach creators outside that circle. Each feed's topics
are its own name plus any extra words you add on the Feeds page.

Pages are fetched in the background, a few seconds apart and at most once per
topic every few hours, and the results are cached in SQLite. Loading reels never
waits for a topic page: until one is cached, it simply adds no leads.
"""
import html as html_text
import json
import logging
import re
import threading
import time
from collections import OrderedDict
from urllib.parse import urlsplit

from . import config, instagram, recommendations

log = logging.getLogger(__name__)

TOPIC_TTL = 6 * 3600              # refresh a topic page at most this often
FAILURE_DELAY = 30 * 60           # wait after a failed fetch before retrying
REQUEST_GAP = 8                   # seconds between topic page requests
MAX_TOPICS = 40                   # topics refreshed per selection
MAX_RELATED = 2                   # sub-topics followed from a broad topic page
MAX_CREATORS_PER_TOPIC = 24
TOPIC_LEAD = 2.2                  # ahead of reel-based leads (credit 2.0), behind a collaborator
TOPIC_TRIAL_SHARE = 0.8           # topic creators come first; reel-based leads get the rest
POPULAR_EVERY = 8                 # at most this many reels between popular topic reels
POPULAR_READY = 2                 # popular reels kept loaded ahead
POPULAR_LOAD_GAP = 8              # seconds between background loads of popular reels

POPULAR_URL = 'https://www.instagram.com/popular/{}/'
EXPLORE_URL = 'https://www.instagram.com/explore/'
SLUG_RE = re.compile(r'[^\W_]+(?:-[^\W_]+)*')


def topic_slug(word: str) -> str:
    """The URL form of a topic: "News politics" → "news-politics"."""
    words = re.split(r'[\s_\-]+', re.sub(r"['’]", '', (word or '').lower()))
    slug = '-'.join(part for part in (re.sub(r'[^\w]|_', '', w) for w in words) if part)
    return slug if SLUG_RE.fullmatch(slug) and len(slug) <= 60 else ''


def parse_topic_page(html: str) -> tuple[dict[str, str], list[str]]:
    """Creators on a topic page as {username: reel code}, plus sub-topic slugs.

    Narrow topics list reels with each creator's username. Broad topics are
    split into sections whose reels carry only numeric ids; their linked
    sub-topic pages are the narrow kind, so they are returned to follow."""
    creators: dict[str, str] = {}
    recognised = False
    related: list[str] = []

    def walk(value):
        if isinstance(value, dict):
            user = value.get('user')
            code = value.get('code')
            if isinstance(user, dict) and isinstance(code, str):
                name = user.get('username')
                if isinstance(name, str) and config.USER_RE.fullmatch(name.lower()):
                    creators.setdefault(name.lower(), code)
            page = value.get('page_url')
            if isinstance(page, str):
                match = re.fullmatch(r'/popular/([^/?#]+)/?', page)
                if match and topic_slug(match.group(1)) and match.group(1) not in related:
                    related.append(topic_slug(match.group(1)))
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    # Narrow pages: xig_logged_out_popular_search_media_info;
    # broad pages: ig_popular_search_category_hub.
    for payload in instagram.sjs_payloads(html, 'popular_search'):
        recognised = recognised or any(isinstance(instagram.nested_find(payload, key), dict) for key in (
            'xig_logged_out_popular_search_media_info', 'ig_popular_search_category_hub'))
        walk(payload)
    if not creators and not related:
        # The page layout may change; sections are also embedded in other payloads.
        for payload in instagram.sjs_payloads(html, '/popular/'):
            walk(payload)
    if not recognised and not creators and not related:
        raise instagram.UnexpectedResponse('Instagram did not return a recognised topic page')
    return dict(list(creators.items())[:MAX_CREATORS_PER_TOPIC]), related


def fetch_topic_page(slug: str) -> tuple[dict[str, str], list[str]]:
    with instagram._new_session() as session:
        response = instagram.request(session, 'GET', POPULAR_URL.format(slug))
    if response.status_code == 404:
        return {}, []
    if response.status_code >= 400:
        raise instagram.UnexpectedResponse(f'Instagram returned HTTP {response.status_code} for topic “{slug}”')
    return parse_topic_page(response.text)


def _schema(connection):
    connection.execute('CREATE TABLE IF NOT EXISTS topic_pages (slug TEXT PRIMARY KEY, fetched REAL NOT NULL, '
                       'creators TEXT NOT NULL, related TEXT NOT NULL, failed REAL)')


class TopicDiscovery:
    """Cached topic pages and the one background worker that refreshes them."""

    def __init__(self, fetch=fetch_topic_page):
        self.fetch = fetch
        self.enabled = False          # the server turns this on; tests stay offline
        self._wanted: list[str] = []
        self._active = None
        self._roots = set()
        self._clips = {}
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None

    def trial_state(self, info, *, network=False):
        """Load only the reel that supplied the topic lead, never its creator's grid."""
        from .reels import REELS
        code = info.get('shortcode')
        if not code:
            return None
        with self._lock:
            cached = self._clips.get(code)
        if cached and time.time() - cached[0] < 900:
            if cached[1]['posts'][0].get('owner') == info.get('username'):
                return cached[1]
        if not network:
            return None
        post = dict(REELS.media(code))
        # Reel details have the video address but not the grid's is_video,
        # image and url fields, which the rest of the feed relies on.
        if not post.get('video'):
            raise instagram.UnexpectedResponse(f'Topic post {code} is not a video')
        owner = (post.get('owner') or '').lower()
        if owner != info.get('username'):
            raise instagram.UnexpectedResponse(f'Topic reel {code} is by @{owner or "unknown"}, not @{info.get("username")}')
        taken_at = post.get('taken_at')
        post.update(shortcode=code, owner=owner, is_video=True,
                    image=post.get('image') or post.get('poster') or '',
                    url=post.get('url') or f'https://www.instagram.com/reel/{code}/',
                    taken_at=int(taken_at) if str(taken_at or '').isdigit() else taken_at)
        state = {'posts': [post], 'has_more': False}
        with self._lock:
            self._clips[code] = (time.time(), state)
            while len(self._clips) > 200:
                del self._clips[next(iter(self._clips))]
        return state

    def waiting(self):
        with self._lock:
            return self.enabled and bool(self._wanted or self._active)

    @staticmethod
    def related_topics(row):
        # Rotate pairs each refresh period, including after a restart. Every
        # linked topic gets a turn rather than permanently favouring the first two.
        related = row[2]
        if not related:
            return []
        start = (int(time.time() // TOPIC_TTL) * MAX_RELATED) % len(related)
        return [related[(start + i) % len(related)] for i in range(min(MAX_RELATED, len(related)))]

    # ---- cache ----
    def _rows(self, slugs):
        if not slugs:
            return {}
        with recommendations.database() as connection:
            _schema(connection)
            marks = ','.join('?' for _ in slugs)
            return {slug: (fetched, json.loads(creators), json.loads(related), failed)
                    for slug, fetched, creators, related, failed in connection.execute(
                        f'SELECT slug, fetched, creators, related, failed FROM topic_pages WHERE slug IN ({marks})',
                        list(slugs))}

    def _store(self, slug, creators=None, related=None, *, failed=False):
        now = time.time()
        with recommendations.database() as connection:
            _schema(connection)
            if failed:
                # Keep the last good result; only remember when to try again.
                connection.execute('INSERT INTO topic_pages(slug, fetched, creators, related, failed) '
                                   "VALUES (?, 0, '{}', '[]', ?) ON CONFLICT(slug) DO UPDATE SET failed=excluded.failed",
                                   (slug, now))
            else:
                connection.execute('INSERT INTO topic_pages(slug, fetched, creators, related, failed) '
                                   'VALUES (?, ?, ?, ?, NULL) ON CONFLICT(slug) DO UPDATE SET fetched=excluded.fetched, '
                                   'creators=excluded.creators, related=excluded.related, failed=NULL',
                                   (slug, now, json.dumps(creators), json.dumps(related)))

    def known_slugs(self) -> list[str]:
        """Every topic slug in the cache: fetched pages and the sub-topics they link to."""
        with recommendations.database() as connection:
            _schema(connection)
            rows = connection.execute('SELECT slug, related FROM topic_pages ORDER BY slug').fetchall()
        found = {}
        for slug, related in rows:
            found[slug] = None
            try:
                linked = json.loads(related)
            except json.JSONDecodeError:
                log.warning('Ignoring unreadable sub-topics cached for “%s”', slug)
                continue
            for sub in linked if isinstance(linked, list) else []:
                if isinstance(sub, str) and topic_slug(sub) == sub:
                    found[sub] = None
        return list(found)

    def request_pages(self, slugs):
        """Queue never-fetched topic pages for the background worker, keeping
        whatever is already queued. Does nothing while offline (tests)."""
        if not self.enabled:
            return
        rows = self._rows(slugs)
        now = time.time()
        due = [slug for slug in slugs if self._due(rows.get(slug), now)]
        with self._lock:
            self._wanted = list(dict.fromkeys(self._wanted + [s for s in due if s != self._active]))
            if self._wanted and (self._worker is None or not self._worker.is_alive()):
                self._worker = threading.Thread(target=self._run, name='ig-topic-discovery', daemon=True)
                self._worker.start()

    # ---- leads ----
    def feed_topics(self, feeds) -> dict[str, list[str]]:
        """Topic slugs for each feed: its name, then its extra words."""
        extra = config.feed_topic_words()
        topics = {}
        for name in feeds:
            slugs = [topic_slug(name)] + [topic_slug(word) for word in extra.get(name, [])]
            topics[name] = list(dict.fromkeys(slug for slug in slugs if slug))
        return topics

    def leads(self, feeds, exclude=()) -> dict[str, dict]:
        """Creators from cached topic pages of these feeds, as
        Each lead retains its topic, feed, creator and exact reel shortcode.
        Stale or missing pages are queued for the background worker."""
        try:
            topics = self.feed_topics(feeds)
            slugs = list(dict.fromkeys(slug for words in topics.values() for slug in words))
            if len(slugs) > MAX_TOPICS:
                start = (int(time.time() // TOPIC_TTL) * MAX_TOPICS) % len(slugs)
                slugs = (slugs[start:] + slugs[:start])[:MAX_TOPICS]
            rows = self._rows(slugs)
            followed = {slug: self.related_topics(row) for slug, row in rows.items()}
            sub_rows = self._rows([s for subs in followed.values() for s in subs if s not in rows])
            rows.update(sub_rows)
        except Exception:
            log.exception('Could not read topic discovery; continuing without it')
            return {}
        self._schedule(slugs, rows)
        exclude = set(exclude)
        found = {}
        for feed, words in topics.items():
            for slug in words:
                if slug not in slugs:
                    continue
                for source in [slug] + followed.get(slug, []):
                    for name, code in (rows.get(source) or (0, {}, [], None))[1].items():
                        if name not in exclude and name not in found:
                            found[name] = {'source': 'popular_topic', 'topic': source.replace('-', ' '), 'feed': feed, 'shortcode': code, 'username': name}
        return found

    # ---- background refresh ----
    def _due(self, row, now):
        if row is None:
            return True
        fetched, _, _, failed = row
        if failed and now - failed < FAILURE_DELAY:
            return False
        return now - fetched >= TOPIC_TTL

    def _schedule(self, slugs, rows):
        if not self.enabled:
            return
        now = time.time()
        due = [slug for slug in slugs if self._due(rows.get(slug), now)]
        # Follow a broad page's sub-topics once it has been fetched.
        for slug in slugs:
            for sub in self.related_topics(rows.get(slug) or (0, {}, [], None)):
                if self._due(rows.get(sub), now):
                    due.append(sub)
        with self._lock:
            self._roots = set(slugs)
            self._wanted = list(dict.fromkeys(slug for slug in due if slug != self._active))
            if not self._wanted:
                return
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._run, name='ig-topic-discovery', daemon=True)
                self._worker.start()

    def _run(self):
        while True:
            with self._lock:
                if not self._wanted:
                    self._worker = None
                    return
                slug = self._wanted.pop(0)
                self._active = slug
            try:
                if not self._due(self._rows([slug]).get(slug), time.time()):
                    continue
                creators, related = self.fetch(slug)
                self._store(slug, creators, related)
                # A broad page may have no clips of its own. Queue its children
                # immediately so an empty feed cannot finish between the two stages.
                children = self.related_topics((time.time(), creators, related, None)) if slug in self._roots else []
                child_rows = self._rows(children)
                due_children = [child for child in children if self._due(child_rows.get(child), time.time())]
                with self._lock:
                    self._wanted = list(dict.fromkeys(self._wanted + due_children))
                log.info('Topic “%s”: %d creators, %d sub-topics', slug, len(creators), len(related))
            except instagram.RateLimited as error:
                log.warning('Topic discovery paused: %s', error)
                self._store(slug, failed=True)
                with self._lock:
                    self._wanted.clear()  # retried after FAILURE_DELAY by later batches
            except Exception as error:
                log.warning('Could not load topic “%s”: %s', slug, error)
                try:
                    self._store(slug, failed=True)
                except Exception:
                    log.exception('Could not record the failed topic “%s”', slug)
            finally:
                with self._lock:
                    self._active = None
            time.sleep(REQUEST_GAP)


TOPICS = TopicDiscovery()


# ---- Trending topics, topic search and suggestions ----
#
# instagram.com/explore/ (and the /popular/ root, which carries the same data)
# lists Instagram's trending topics in named sections, plus a few trending
# creators. Together with the sub-topics linked from cached topic pages, they
# are every topic the app knows: search and suggestions read only these, so
# typing in the search box never sends a request to Instagram.

TRENDING_TTL = 3 * 3600           # refetch /explore/ at most this often
TRENDING_RETRY = 5 * 60           # after a failed fetch, wait this long before trying again
TOPIC_PAGE_TTL = 15 * 60          # an opened topic page is reused this long
MAX_OPEN_TOPIC_PAGES = 32
MAX_TOPIC_REELS = 60
SEARCH_LIMIT = 10
MAX_SUGGESTIONS = 12
MAX_TAG_ACCOUNTS = 500
IMAGE_HOSTS = ('.cdninstagram.com', '.fbcdn.net')
POST_CODE_RE = re.compile(r'[A-Za-z0-9_-]{5,30}')
# Hashtags on almost every reel, which say nothing about a feed's subject.
COMMON_TAGS = frozenset({
    'fyp', 'foryou', 'foryoupage', 'fy', 'viral', 'viralreels', 'viralvideo', 'trending', 'trend',
    'reels', 'reel', 'reelsinstagram', 'instareels', 'instagram', 'instagood', 'insta', 'explore',
    'explorepage', 'explorepage✨', 'love', 'like', 'likeforlikes', 'follow', 'followme', 'video',
    # Sponsorship labels, not subjects.
    'ad', 'ads', 'sponsored', 'paidpartnership', 'partner', 'collab', 'gifted', 'promo', 'advert',
})


def count_label(count, one: str, many: str) -> str | None:
    """A count the way Instagram's topic directory writes it: 18 posts, 349K posts, 114M posts."""
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return None
    if count < 1000:
        return f'{count} {one if count == 1 else many}'
    units = ((1_000_000_000, 'B'), (1_000_000, 'M'), (1_000, 'K'))
    for index, (size, suffix) in enumerate(units):
        if count >= size:
            shown = int(count / size + 0.5)
            if shown >= 1000 and index:  # 999,600 is 1M, not 1000K
                size, suffix = units[index - 1]
                shown = int(count / size + 0.5)
            return f'{shown:,}{suffix} {many}'


def instagram_image(url) -> str | None:
    """url when it is an https image on Instagram's own CDN, else None."""
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname or ''
    except ValueError:
        return None
    if parts.scheme != 'https' or parts.username or parts.password or not host.endswith(IMAGE_HOSTS):
        return None
    return url


def readable_name(text: str) -> str:
    """"one piece" → "One Piece", the way Instagram titles its topics."""
    return ' '.join(word[:1].upper() + word[1:] for word in text.replace('-', ' ').split())


def topic_item(word, *, slug: str = '', name=None, count=None, image=None) -> dict | None:
    """One topic for the API, or None when it cannot be saved as a feed topic.

    word is what a feed stores and always turns into slug. When Instagram
    links a page whose slug the keyword does not produce, the slug wins."""
    word = ' '.join(str(word or '').lower().split())
    if slug and topic_slug(word) != slug:
        word = slug.replace('-', ' ')
    slug = topic_slug(word)
    if not slug or not config.TOPIC_WORD_RE.fullmatch(word):
        return None
    name = ' '.join(name.split()) if isinstance(name, str) else ''
    return {'name': name or readable_name(word), 'slug': slug, 'word': word,
            'count_label': count_label(count, 'post', 'posts'), 'image': instagram_image(image)}


def _popular_slug(url) -> str:
    match = re.fullmatch(r'/popular/([^/?#]+)/?', url) if isinstance(url, str) else None
    return topic_slug(match.group(1)) if match else ''


def _explore_topic(node: dict) -> dict | None:
    keyword = node.get('keyword')
    if not isinstance(keyword, dict) or not isinstance(keyword.get('keyword_text'), str):
        return None
    text = keyword['keyword_text']
    display = node.get('display_name')
    return topic_item(text, slug=_popular_slug(keyword.get('page_url') or node.get('page_url')),
                      name=display if isinstance(display, str) and display.strip() else readable_name(text),
                      count=keyword.get('media_count'), image=keyword.get('thumbnail_url'))


def _explore_creator(user: dict) -> dict | None:
    username = user.get('username')
    if not isinstance(username, str) or not config.USER_RE.fullmatch(username):
        return None
    picture = user.get('profile_picture_for_dimensions')
    picture = picture.get('uri') if isinstance(picture, dict) else user.get('profile_pic_url')
    full_name = user.get('full_name')
    return {'username': username.lower(),
            'full_name': ' '.join(full_name.split()) if isinstance(full_name, str) else '',
            'followers_label': count_label(user.get('follower_count'), 'follower', 'followers'),
            'profile_pic_url': instagram_image(picture)}


def parse_explore_page(html: str) -> dict:
    """Instagram's topic directory: {"sections": [{"title", "topics"}], "creators": [...]}.

    Entries Instagram sends incomplete (no keyword, an invalid username or
    slug) are left out; a page without the directory raises UnexpectedResponse."""
    found = None
    for payload in instagram.sjs_payloads(html, 'popular_search_home_sections'):
        found = instagram.nested_find(payload, 'popular_search_home_sections')
        if isinstance(found, list):
            break
    if not isinstance(found, list):
        raise instagram.UnexpectedResponse('Instagram did not return its trending topics')
    sections, creators, seen_creators = [], [], set()
    for section in found:
        if not isinstance(section, dict):
            continue
        connection = section.get('entries_connection')
        edges = connection.get('edges') if isinstance(connection, dict) else None
        listed, seen = [], set()
        for edge in edges if isinstance(edges, list) else []:
            node = edge.get('node') if isinstance(edge, dict) else None
            if not isinstance(node, dict):
                continue
            if isinstance(node.get('user'), dict):
                creator = _explore_creator(node['user'])
                if creator and creator['username'] not in seen_creators:
                    seen_creators.add(creator['username'])
                    creators.append(creator)
                continue
            item = _explore_topic(node)
            if item and item['slug'] not in seen:
                seen.add(item['slug'])
                listed.append(item)
        title = section.get('display_title')
        if listed and isinstance(title, str) and title.strip():
            sections.append({'title': ' '.join(title.split()), 'topics': listed})
    if not sections and not creators:
        raise instagram.UnexpectedResponse('Instagram returned no trending topics')
    return {'sections': sections, 'creators': creators}


def fetch_explore_page() -> dict:
    with instagram._new_session() as session:
        response = instagram.request(session, 'GET', EXPLORE_URL)
    if response.status_code >= 400:
        raise instagram.UnexpectedResponse(f'Instagram returned HTTP {response.status_code} for its trending topics')
    return parse_explore_page(response.text)


def _topic_reel(node: dict) -> dict | None:
    code = node.get('code')
    if not isinstance(code, str) or not POST_CODE_RE.fullmatch(code):
        return None
    typename = node.get('__typename') or ''
    if not (isinstance(typename, str) and typename.startswith('XIGPolaris')) and 'display_uri' not in node:
        return None
    user = node.get('user') if isinstance(node.get('user'), dict) else {}
    owner = user.get('username')
    owner = owner.lower() if isinstance(owner, str) and config.USER_RE.fullmatch(owner) else None
    caption = node.get('caption')
    is_video = typename == 'XIGPolarisVideoMedia' or bool(node.get('video_versions'))

    def number(key):
        value = node.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    return {'shortcode': code, 'owner': owner, 'is_video': is_video, 'is_carousel': False,
            'url': f'https://www.instagram.com/{"reel" if is_video else "p"}/{code}/',
            'image': instagram_image(node.get('display_uri')) or '',
            'like_count': number('like_count'), 'play_count': number('play_count'),
            'comment_count': number('comment_count'), 'taken_at': number('taken_at'),
            'caption': caption.get('text') if isinstance(caption, dict) else None}


def parse_topic_reels(html: str) -> dict:
    """The popular reels on one topic page, with the page's name and reel count.

    Narrow topics list reels with their creators; broad topics list reels per
    sub-topic, without creator names (the viewer looks those up per reel)."""
    creators, related = parse_topic_page(html)  # raises when the page is not a topic page
    posts, seen = [], set()

    def walk(value):
        if isinstance(value, dict):
            reel = _topic_reel(value) if 'code' in value else None
            if reel and reel['shortcode'] not in seen:
                seen.add(reel['shortcode'])
                posts.append(reel)
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for payload in instagram.sjs_payloads(html, 'popular_search'):
        walk(payload)
    name = label = None
    title = re.search(r'<title>([^<]*)</title>', html)
    if title:
        text = ' '.join(html_text.unescape(title.group(1)).split()).removesuffix(' on Instagram')
        text, _, count = text.partition(' • ')
        name = text.strip() or None
        label = count.strip() if re.fullmatch(r'[\d.,]+[KMB]? (?:reels?|posts?)', count.strip()) else None
    return {'name': name, 'count_label': label, 'posts': posts[:MAX_TOPIC_REELS],
            'creators': creators, 'related': related}


def fetch_topic_reels(slug: str) -> dict:
    with instagram._new_session() as session:
        response = instagram.request(session, 'GET', POPULAR_URL.format(slug))
    if response.status_code == 404:
        return {'name': None, 'count_label': None, 'posts': [], 'creators': {}, 'related': []}
    if response.status_code >= 400:
        raise instagram.UnexpectedResponse(f'Instagram returned HTTP {response.status_code} for topic “{slug}”')
    return parse_topic_reels(response.text)


def _directory_schema(connection):
    connection.execute('CREATE TABLE IF NOT EXISTS trending_topics '
                       '(id INTEGER PRIMARY KEY CHECK (id = 1), fetched REAL NOT NULL, data TEXT NOT NULL)')


class TopicDirectory:
    """Every topic the app knows, for the trending list, search and suggestions.

    The trending list is fetched from /explore/ at most every few hours and
    kept in memory and in SQLite, so a restart or a failed fetch still has
    the last good copy."""

    def __init__(self, discovery: TopicDiscovery, fetch=fetch_explore_page, fetch_page=fetch_topic_reels):
        self.discovery = discovery
        self.fetch = fetch
        self.fetch_page = fetch_page
        self._data: dict | None = None
        self._failed_at = 0.0
        self._error: Exception | None = None
        self._lock = threading.Lock()        # one /explore/ fetch at a time
        self._pages = OrderedDict()          # slug -> (time, topic page)
        self._pages_lock = threading.Lock()
        self._refresher: threading.Thread | None = None

    # ---- trending ----
    def cached(self) -> dict | None:
        """The last good trending list, from memory or disk; never fetches."""
        if self._data is not None:
            return self._data
        with recommendations.database() as connection:
            _directory_schema(connection)
            row = connection.execute('SELECT fetched, data FROM trending_topics WHERE id = 1').fetchone()
        if row is None:
            return None
        try:
            data = json.loads(row[1])
            if not isinstance(data.get('sections'), list) or not isinstance(data.get('creators'), list):
                raise ValueError('missing sections')
        except (ValueError, AttributeError) as error:
            log.warning('Ignoring the unreadable saved trending topics: %s', error)
            return None
        self._data = {**data, 'fetched_at': int(row[0])}
        return self._data

    def _fresh(self, data) -> bool:
        return bool(data) and time.time() - data['fetched_at'] < TRENDING_TTL

    def trending(self) -> dict:
        """The trending list, fetched when the copy is older than a few hours.
        A failed fetch serves the last good copy, or raises when there is none."""
        data = self.cached()
        if self._fresh(data):
            return data
        with self._lock:
            data = self.cached()  # another request may have fetched it meanwhile
            if self._fresh(data):
                return data
            if self._error is not None and time.time() - self._failed_at < TRENDING_RETRY:
                if data:
                    return data
                raise self._error.with_traceback(None)
            try:
                fetched = self.fetch()
            except Exception as error:
                self._failed_at, self._error = time.time(), error
                if not isinstance(error, instagram.InstagramError):
                    log.error('Unexpected failure fetching trending topics', exc_info=error)
                if not data:
                    raise
                log.warning('Could not refresh trending topics; showing the copy from %s: %s',
                            time.strftime('%Y-%m-%d %H:%M', time.localtime(data['fetched_at'])), error)
                return data
            data = {'sections': fetched['sections'], 'creators': fetched['creators'], 'fetched_at': int(time.time())}
            self._data, self._error, self._failed_at = data, None, 0.0
            try:
                with recommendations.database() as connection:
                    _directory_schema(connection)
                    connection.execute('INSERT OR REPLACE INTO trending_topics(id, fetched, data) VALUES (1, ?, ?)',
                                       (data['fetched_at'], json.dumps({'sections': data['sections'],
                                                                        'creators': data['creators']})))
            except Exception:
                log.exception('Could not save trending topics; keeping them in memory only')
            log.info('Trending topics: %d sections, %d creators', len(data['sections']), len(data['creators']))
            return data

    def refresh_in_background(self) -> None:
        """Start fetching a stale trending list without waiting for it. Offline in tests."""
        if not self.discovery.enabled or self._fresh(self._data):
            return
        if self._error is not None and time.time() - self._failed_at < TRENDING_RETRY:
            return
        with self._pages_lock:
            if self._refresher is not None and self._refresher.is_alive():
                return

            def run():
                try:
                    self.trending()
                except instagram.InstagramError as error:
                    log.info('Background refresh of trending topics failed: %s', error)
                except Exception:
                    log.exception('Background refresh of trending topics failed')
            self._refresher = threading.Thread(target=run, name='ig-trending-topics', daemon=True)
            self._refresher.start()

    # ---- everything known ----
    def known_topics(self) -> dict[str, dict]:
        """slug -> topic: trending topics first (with names and counts), then
        every slug cached by topic discovery. Never fetches."""
        known: dict[str, dict] = {}
        for section in (self.cached() or {}).get('sections', []):
            for item in section.get('topics', []):
                known.setdefault(item['slug'], item)
        for slug in self.discovery.known_slugs():
            if slug not in known:
                item = topic_item(slug.replace('-', ' '))
                if item:
                    known[slug] = item
        return known

    def search(self, text: str, limit: int = SEARCH_LIMIT) -> list[dict]:
        """Known topics whose name or slug contains the text, names starting
        with it first, then names with a word starting with it."""
        query = ' '.join(text.lower().split())
        if not query:
            return []
        self.refresh_in_background()
        query_slug = topic_slug(query)
        matches = []
        for index, item in enumerate(self.known_topics().values()):
            name, slug = item['name'].lower(), item['slug']
            if name.startswith(query) or (query_slug and slug.startswith(query_slug)):
                rank = 0
            elif f' {query}' in f' {name}' or (query_slug and f'-{query_slug}' in f'-{slug}'):
                rank = 1
            elif query in name or (query_slug and query_slug in slug):
                rank = 2
            else:
                continue
            matches.append((rank, len(slug), index, item))
        matches.sort(key=lambda match: match[:3])
        return [item for *_, item in matches[:limit]]

    def lookup(self, slug: str) -> dict | None:
        try:
            return self.known_topics().get(slug)
        except Exception:
            log.exception('Could not read known topics for “%s”', slug)
            return None

    # ---- suggestions ----
    def _feed_tags(self, accounts: list[str]) -> list[tuple[str, int]]:
        """Hashtags on the feed's stored reels as (tag, accounts using it), most shared first."""
        accounts = [name.lower() for name in accounts][:MAX_TAG_ACCOUNTS]
        if not accounts:
            return []
        marks = ','.join('?' for _ in accounts)
        with recommendations.database() as connection:
            rows = connection.execute(
                f'SELECT tag, COUNT(DISTINCT owner) AS owners, COUNT(*) AS reels FROM tag_index '
                f'WHERE owner IN ({marks}) GROUP BY tag HAVING reels >= 2 '
                'ORDER BY owners DESC, reels DESC, tag LIMIT 80', accounts).fetchall()
        return [(tag, owners) for tag, owners, _ in rows if tag not in COMMON_TAGS and len(tag) >= 3]

    def suggest(self, feed: str) -> list[dict]:
        """Topics to add to a feed, each with its reason: "related" (linked
        from the feed's topic pages) or "hashtag" (common in the feed's reels).
        Raises KeyError for an unknown feed."""
        feeds = config.load_feeds()
        if feed not in feeds:
            raise KeyError(feed)
        own = self.discovery.feed_topics([feed])[feed]
        excluded = set(own) | {slug.replace('-', '') for slug in own}
        known = self.known_topics()
        suggestions, seen = [], set()

        def add(item, reason):
            if (item and item['slug'] not in seen and item['slug'] not in excluded
                    and item['slug'].replace('-', '') not in excluded and len(suggestions) < MAX_SUGGESTIONS):
                seen.add(item['slug'])
                suggestions.append({**item, 'reason': reason})

        rows = self.discovery._rows(own)
        # Pages never fetched (or only failed) are fetched in the background for next time.
        self.discovery.request_pages([slug for slug in own if not (rows.get(slug) or (0,))[0]])
        for slug in own:
            for sub in (rows.get(slug) or (0, {}, [], None))[2]:
                if isinstance(sub, str):
                    add(known.get(sub) or topic_item(sub.replace('-', ' ')), 'related')

        by_tag = {}
        for item in known.values():
            by_tag.setdefault(item['slug'].replace('-', ''), item)
        for tag, owners in self._feed_tags(config.accounts_from_feed(feeds[feed])):
            # A known topic for the tag, else the tag itself when several of the feed's creators use it.
            add(by_tag.get(tag) or (topic_item(tag) if owners >= 2 else None), 'hashtag')

        # General trending topics are not suggested here: they rarely fit a
        # feed. They are on the trending strip and in search instead.
        return suggestions

    # ---- topic pages ----
    def topic_page(self, slug: str) -> dict:
        """A topic's popular reels with its name and count, reused for a few minutes."""
        with self._pages_lock:
            cached = self._pages.get(slug)
        if cached and time.time() - cached[0] < TOPIC_PAGE_TTL:
            return cached[1]
        page = self.fetch_page(slug)
        if page['creators'] or page['related']:
            try:  # the page also refreshes topic discovery's cache
                self.discovery._store(slug, page['creators'], page['related'])
            except Exception:
                log.exception('Could not cache the creators of topic “%s”', slug)
        with self._pages_lock:
            self._pages[slug] = (time.time(), page)
            self._pages.move_to_end(slug)
            while len(self._pages) > MAX_OPEN_TOPIC_PAGES:
                self._pages.popitem(last=False)
        return page


DIRECTORY = TopicDirectory(TOPICS)
