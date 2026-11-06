"""Reels-only pagination, separate from the photo/profile grid."""
import logging
import random
import sqlite3
import threading
import time
from collections import OrderedDict

from . import config, instagram

log = logging.getLogger(__name__)


class ReelCache(instagram.ProfileCache):
    STORED_KEYS = instagram.ProfileCache.STORED_KEYS + (
        "pending", "page_has_more", "latest_cursor", "latest_codes", "seen_codes",
        "archive_cursors", "archive_failures", "archive_retry_at", "archive_reset_pending")

    @property
    def path(self):
        return config.CACHE / "reels.sqlite3"


    def fresh_usernames(self):
        """Retained listing metadata can fill startup while latest pages refresh."""
        if not self.path.exists():
            return set()
        try:
            with self._database() as connection:
                return {row[0] for row in connection.execute(
                    'SELECT username FROM profiles WHERE fetched > ?', (time.time() - self.KEEP_FOR,))}
        except (sqlite3.Error, RuntimeError):
            log.exception('Could not read the reels cache')
            return set()


class ReelStore(instagram.ProfileStore):
    # Small steps let the feed use completed details without waiting for 12 posts.
    DETAILS_PER_STEP = 3

    def __init__(self):
        super().__init__(ReelCache())
        self.media_cache = OrderedDict()
        self.media_lock = threading.Lock()
        self.query_context = {}
        self.query_lock = threading.Lock()

    def media(self, code):
        with self.media_lock:
            cached = self.media_cache.get(code)
            if cached and time.monotonic() - cached[0] < 900:
                return dict(cached[1])
        data = instagram.media_info(code)
        with self.media_lock:
            self.media_cache[code] = (time.monotonic(), dict(data))
            self.media_cache.move_to_end(code)
            while len(self.media_cache) > 200:
                self.media_cache.popitem(last=False)
        return data

    MAX_PENDING = 240
    MAX_POSTS = 120
    MAX_KNOWN_CODES = 720
    ARCHIVE_RETRY_SECONDS = 60 * 60

    def cached(self, username):
        """Retained metadata is immediately usable; latest refresh is separate."""
        state = self.memory.get(username)
        if state is None:
            state = self.cache.load(username, self.cache.KEEP_FOR)
            if state is not None:
                self.memory[username] = state
        if state is not None:
            self._prepare(state)
        return state

    def load(self, username):
        state = self.cached(username)
        if state is not None and not self.is_stale(state):
            return state, False
        try:
            return self.fetch(username), True
        except instagram.RateLimited:
            if state is None:
                raise
            return state, True

    @staticmethod
    def _prepare(state):
        state.setdefault('posts', [])
        state.setdefault('pending', [])
        state.setdefault('page_has_more', bool(state.get('has_more') and state.get('cursor')))
        # Old caches have no age labels. Treat their loaded set as recent until
        # the next latest-page refresh supplies an authoritative classification.
        for post in state['posts'] + state['pending']:
            post.setdefault('reel_age_group', 'recent')
        if state.get('latest_codes') is None:
            state['latest_codes'] = [p['shortcode'] for p in state['posts'] + state['pending']
                                     if p.get('reel_age_group') == 'recent']
        if state.get('seen_codes') is None:
            state['seen_codes'] = list(dict.fromkeys(p['shortcode'] for p in state['posts'] + state['pending']))
        if state.get('archive_cursors') is None:
            state['archive_cursors'] = [state['cursor']] if state.get('cursor') else []
        state.setdefault('lock', threading.Lock())

    def _trim(self, state):
        latest = set(state['latest_codes'])
        for key, limit in (('posts', self.MAX_POSTS), ('pending', self.MAX_PENDING)):
            entries = list({item['shortcode']: item for item in state[key]}.values())
            for item in entries:
                item['reel_age_group'] = 'recent' if item['shortcode'] in latest else 'older'
            if len(entries) > limit:
                recent = [item for item in entries if item['reel_age_group'] == 'recent'][:limit]
                older = [item for item in entries if item['reel_age_group'] == 'older']
                entries = recent + (older[-(limit - len(recent)):] if len(recent) < limit else [])
            state[key] = entries
        state['seen_codes'] = list(dict.fromkeys(state['seen_codes']))[-self.MAX_KNOWN_CODES:]
        state['archive_cursors'] = state['archive_cursors'][-64:]
        state['has_more'] = bool(state['pending'] or state['page_has_more'])

    def _query(self, username, after):
        # Anonymous context is shared; no profile-grid calls are needed here.
        with self.query_lock:
            if time.monotonic() - self.query_context.get('created_at', time.monotonic()) > 900:
                self.query_context.clear()
            try:
                page = instagram.reels_tab(username, after=after, context=self.query_context)
                self.query_context.setdefault('created_at', time.monotonic())
                return page
            except instagram.InstagramError:
                self.query_context.clear()
                raise

    def _page(self, username, state, *, latest=False, restart_archive=False):
        self._prepare(state)
        previous = None if latest else state.get('cursor')
        page = self._query(username, previous)
        if latest:
            state['latest_codes'] = list(dict.fromkeys(item['shortcode'] for item in page['reels']))
            state['latest_cursor'] = page.get('cursor')
        seen = set(state['seen_codes']) | {p['shortcode'] for p in state['posts'] + state['pending']}
        pending = []
        for listing in page['reels']:
            code = listing['shortcode']
            if code not in seen:
                item = {key: listing.get(key) for key in ('shortcode', 'image', 'url', 'play_count')}
                item['reel_age_group'] = 'recent' if code in state['latest_codes'] else 'older'
                pending.append(item)
                seen.add(code)
                state['seen_codes'].append(code)
        if len(pending) > self.DETAILS_PER_STEP:
            random.shuffle(pending)
        state['pending'].extend(pending)
        # Latest refresh must not replace an archive cursor pointing many pages
        # deeper. Reset only on first load or after bounded invalid-cursor recovery.
        if not latest or restart_archive:
            cursor = page.get('cursor')
            repeated = bool(cursor and not restart_archive and cursor in state['archive_cursors'])
            state['cursor'] = cursor
            state['page_has_more'] = bool(page['has_more'] and cursor and not repeated)
            if restart_archive:
                state['archive_cursors'] = []
                state['archive_reset_pending'] = False
                state['archive_failures'] = 0
                state['archive_retry_at'] = 0
            if cursor:
                state['archive_cursors'].append(cursor)
            if repeated and page['has_more']:
                state['archive_reset_pending'] = True
                state['archive_retry_at'] = time.time() + self.ARCHIVE_RETRY_SECONDS
        self._trim(state)

    @staticmethod
    def can_explore(state):
        if not state or time.time() < (state.get('archive_retry_at') or 0):
            return False
        return bool(state.get('archive_reset_pending') or
                    (state.get('cursor') and state.get('page_has_more')))

    def _advance_archive(self, username, state):
        if not self.can_explore(state):
            return
        try:
            if state.get('archive_reset_pending'):
                self._page(username, state, latest=True, restart_archive=True)
                state['fetched_at'] = time.time()
            else:
                self._page(username, state)
                state['archive_failures'] = 0
                if not state.get('archive_reset_pending'):
                    state['archive_retry_at'] = 0
        except instagram.UnexpectedResponse as error:
            # A bad cursor must not kill a creator with usable cached clips.
            # Never reset for a rate limit or network failure, and never retry
            # within this call. The next scheduled attempt can re-bootstrap.
            failures = (state.get('archive_failures') or 0) + 1
            state['archive_failures'] = failures
            state['archive_reset_pending'] = failures >= 2
            state['archive_retry_at'] = time.time() + (self.ARCHIVE_RETRY_SECONDS if failures >= 2 else 30)
            log.info('Could not advance older reels for @%s: %s', username, error)

    def _details(self, username, state, *, older_only=False):
        self._prepare(state)
        fresh = []
        for _ in range(min(self.DETAILS_PER_STEP, len(state['pending']))):
            recent = [p for p in state['pending'] if p['reel_age_group'] == 'recent']
            older = [p for p in state['pending'] if p['reel_age_group'] == 'older']
            if older_only:
                if not older:
                    break
                listing = random.choice(older)
            elif older and (not recent or random.random() < .3):
                listing = random.choice(older)
            else:
                listing = recent[0]
            code = listing['shortcode']
            instagram.BACKOFF.check()
            try:
                data = self.media(code)
            except instagram.ProfileUnavailable:
                state['pending'].remove(listing)
                continue
            except instagram.InstagramError as error:
                listing['failures'] = listing.get('failures', 0) + 1
                state['pending'].remove(listing)
                if listing['failures'] < 3:
                    state['pending'].append(listing)
                else:
                    log.warning('Skipping reel %s after three detail failures: %s', code, error)
                self._trim(state)
                if instagram.BACKOFF.remaining():
                    raise
                continue
            state['pending'].remove(listing)
            if not (data.get('video') or data.get('video_dash_manifest')):
                continue
            item = {key: data.get(key) for key in (
                'shortcode', 'owner', 'caption', 'audio_id', 'taken_at',
                'like_count', 'comment_count', 'coauthors', 'audio_creator', 'paid_partnership')}
            item.update(shortcode=code, owner=data.get('owner') or username,
                        is_video=True, image=data.get('poster') or listing.get('image'),
                        url=listing.get('url'), play_count=listing.get('play_count'),
                        reel_age_group=listing['reel_age_group'])
            state['posts'].append(item)
            fresh.append(item)
        self._trim(state)
        return fresh

    def fetch(self, username):
        state = self.cached(username)
        initial = state is None
        if initial:
            state = {'username': username, 'biography': '', 'posts': [], 'pending': [],
                     'cursor': None, 'has_more': True, 'page_has_more': True,
                     'lock': threading.Lock()}
        with state['lock']:
            self._page(username, state, latest=True, restart_archive=initial)
            state['fetched_at'] = time.time()
            self.memory[username] = state
            try:
                self._details(username, state)
            finally:
                self.cache.save(username, state)
        return state

    def more(self, username):
        state = self.cached(username)
        if state is None:
            state = self.fetch(username)
            return {'posts': list(state['posts']), 'has_more': state['has_more']}
        with state['lock']:
            try:
                if not state['pending']:
                    self._advance_archive(username, state)
                fresh = self._details(username, state)
                return {'posts': fresh, 'has_more': state['has_more']}
            finally:
                self.cache.save(username, state)

    def explore(self, username):
        """One older listing page and up to three details, without draining recent work."""
        state = self.cached(username)
        if state is None:
            state = self.fetch(username)
            return {'posts': list(state['posts']), 'has_more': state['has_more']}
        with state['lock']:
            try:
                self._advance_archive(username, state)
                fresh = self._details(username, state, older_only=True)
                return {'posts': fresh, 'has_more': state['has_more']}
            finally:
                self.cache.save(username, state)


REELS = ReelStore()
STATE = REELS.memory


def more_reels(username):
    return REELS.more(username)


def media_info(code):
    """Reuse post details fetched for ranking; media URLs stay in memory only."""
    return REELS.media(code)
