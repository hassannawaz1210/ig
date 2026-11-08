"""New creators from Instagram's public topic pages (instagram.com/popular/<topic>/).

Leads from captions, tags and comments only ever reach creators connected to
the ones already in your feeds. Topic pages list popular reels for a topic
from anyone, so they reach creators outside that circle. Each feed's topics
are its own name plus any extra words you add on the Feeds page.

Pages are fetched in the background, a few seconds apart and at most once per
topic every few hours, and the results are cached in SQLite. Loading reels never
waits for a topic page: until one is cached, it simply adds no leads.
"""
import json
import logging
import re
import threading
import time

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

POPULAR_URL = 'https://www.instagram.com/popular/{}/'
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
