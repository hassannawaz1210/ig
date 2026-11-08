"""New creators from Instagram's "Accounts you might like" suggestions.

For each creator in your selected feeds, Instagram suggests similar accounts
(the row shown above a profile's posts). These are the main source of new
creators: Instagram has already judged them similar, unlike accounts that
merely appear in captions. An account suggested by several of your creators
ranks higher, and it belongs to the feed of the creator that suggested it.

Suggestions are fetched in the background, a few seconds apart, cached in
SQLite for a day, and loading reels never waits for them.

The map grows through accounts you enjoy ("earned expansion"): once you like,
save or keep watching a suggested account, its own suggestions are fetched
too (a second hop). Accounts never earn a third hop, which keeps the map in
the niche you watch instead of drifting. An account suggested by more of the
accounts in the map ranks higher, and each feed's map is capped.
"""
import json
import logging
import random
import threading
import time

from . import config, history, instagram, recommendations

log = logging.getLogger(__name__)

SUGGESTION_TTL = 24 * 3600    # refresh one creator's suggestions at most daily
FAILURE_DELAY = 60 * 60       # wait after a failed fetch before retrying
REQUEST_GAP = 12              # seconds between suggestion lookups (two requests each)
MAX_SOURCES = 40              # feed creators whose suggestions are kept fresh
SUGGESTED_LEAD = 2.4          # ahead of popular topics (2.2) and caption credits (2.0)
SUGGESTED_TRIAL_SHARE = 0.7   # share of new-creator turns given to suggested accounts
SUGGESTED_READY = 3           # suggested creators kept loaded ahead
SUGGESTED_LOAD_GAP = 10       # seconds between background loads of suggested creators
SUGGESTED_REELS = 3           # reels one suggested creator may supply per 20 viewed
DISCOVERY_AFTER = 1           # reels from your feeds before the next new-creator turn
MAX_HOPS = 2                  # feed creator -> suggestion -> its suggestions (if earned)
EARN_SECONDS = 20             # total seconds watched that earns an account a lookup
MAX_EXPANDED = 20             # earned accounts whose suggestions are kept fresh
MAX_MAP_PER_FEED = 300        # suggested accounts kept per feed, most-suggested first


def earned_accounts(candidates) -> set[str]:
    """Suggested accounts you liked, saved, or watched for EARN_SECONDS or more."""
    candidates = set(candidates)
    if not candidates:
        return set()
    earned = set()
    with recommendations.database() as connection:
        for (raw,) in connection.execute("SELECT r.data FROM reels r JOIN feedback f ON f.code=r.code "
                                         "WHERE f.action='like'"):
            owner = (json.loads(raw).get('owner') or '').lower()
            if owner in candidates:
                earned.add(owner)
        earned |= {row[0].lower() for row in connection.execute('SELECT DISTINCT owner FROM saved_reels')
                   if row[0] and row[0].lower() in candidates}
    with history.database() as db:
        earned |= {owner.lower() for owner, seconds in db.execute(
            'SELECT owner, SUM(seconds) FROM viewings GROUP BY owner') if owner and owner.lower() in candidates
            and (seconds or 0) >= EARN_SECONDS}
    return earned


def _schema(connection):
    connection.execute('CREATE TABLE IF NOT EXISTS suggested_accounts (source TEXT PRIMARY KEY, '
                       'fetched REAL NOT NULL, accounts TEXT NOT NULL, failed REAL)')


class SuggestionDiscovery:
    """Cached suggestions per feed creator and the worker that refreshes them."""

    def __init__(self, fetch=None):
        self.fetch = fetch or (lambda source: [a['username'] for a in instagram.related_accounts(source)])
        self.enabled = False          # the server turns this on; tests stay offline
        self._wanted: list[str] = []
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None

    # ---- cache ----
    def _rows(self, sources):
        if not sources:
            return {}
        with recommendations.database() as connection:
            _schema(connection)
            marks = ','.join('?' for _ in sources)
            return {source: (fetched, json.loads(accounts), failed)
                    for source, fetched, accounts, failed in connection.execute(
                        f'SELECT source, fetched, accounts, failed FROM suggested_accounts WHERE source IN ({marks})',
                        list(sources))}

    def _store(self, source, accounts=None, *, failed=False):
        now = time.time()
        with recommendations.database() as connection:
            _schema(connection)
            if failed:
                # Keep the last good list; only remember when to try again.
                connection.execute("INSERT INTO suggested_accounts(source, fetched, accounts, failed) "
                                   "VALUES (?, 0, '[]', ?) ON CONFLICT(source) DO UPDATE SET failed=excluded.failed",
                                   (source, now))
            else:
                connection.execute('INSERT INTO suggested_accounts(source, fetched, accounts, failed) '
                                   'VALUES (?, ?, ?, NULL) ON CONFLICT(source) DO UPDATE SET '
                                   'fetched=excluded.fetched, accounts=excluded.accounts, failed=NULL',
                                   (source, now, json.dumps(accounts)))

    # ---- leads ----
    def leads(self, selected: dict, exclude=()) -> dict[str, dict]:
        """Suggested accounts for these feeds, as {username: {'source': 'suggested',
        'via': account, 'feed': feed, 'count': n, 'hop': 1 or 2}}. Hop 2 accounts
        come from suggested accounts you enjoyed. Missing or stale lists are
        queued for the background worker."""
        exclude = set(exclude)
        owners = {}
        for feed, accounts in selected.items():
            for account in accounts:
                owners.setdefault(account, feed)
        # A random subset each time, so large feeds still get every creator refreshed.
        sources = list(owners)
        random.shuffle(sources)
        sources = sources[:MAX_SOURCES]
        try:
            rows = self._rows(sources)
            first = {}   # hop-1 account -> (feed, via)
            for source in sources:
                for name in (rows.get(source) or (0, [], None))[1]:
                    if name not in owners:
                        first.setdefault(name, (owners[source], source))
            expanded = []
            if MAX_HOPS > 1:
                expanded = sorted(earned_accounts(first))
                random.shuffle(expanded)
                expanded = expanded[:MAX_EXPANDED]
                rows.update(self._rows([name for name in expanded if name not in rows]))
        except Exception:
            log.exception('Could not read suggested accounts; continuing without them')
            return {}
        self._schedule(sources + expanded, rows)
        found = {}
        lists = [(source, owners[source], 1) for source in sources] + \
                [(source, first[source][0], 2) for source in expanded]
        for source, feed, hop in lists:
            for name in (rows.get(source) or (0, [], None))[1]:
                if name in exclude or name in owners or not config.USER_RE.fullmatch(name):
                    continue
                if name in found:
                    found[name]['count'] += 1   # suggested by more accounts in the map
                else:
                    found[name] = {'source': 'suggested', 'via': source, 'feed': feed, 'count': 1, 'hop': hop}
        # Keep each feed's map to its most-suggested accounts.
        kept = {}
        for feed in {info['feed'] for info in found.values()}:
            ranked = sorted((name for name, info in found.items() if info['feed'] == feed),
                            key=lambda name: (-found[name]['count'], found[name]['hop'], name))
            kept.update({name: found[name] for name in ranked[:MAX_MAP_PER_FEED]})
        return kept

    @staticmethod
    def strength(info) -> float:
        """Accounts suggested by more of your creators rank higher."""
        return SUGGESTED_LEAD + min(0.6, 0.2 * (info.get('count', 1) - 1))

    # ---- background refresh ----
    @staticmethod
    def _due(row, now):
        if row is None:
            return True
        fetched, _, failed = row
        if failed and now - failed < FAILURE_DELAY:
            return False
        return now - fetched >= SUGGESTION_TTL

    def _schedule(self, sources, rows):
        if not self.enabled:
            return
        now = time.time()
        due = [source for source in sources if self._due(rows.get(source), now)]
        if not due:
            return
        with self._lock:
            self._wanted = list(dict.fromkeys(self._wanted + due))
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._run, name='ig-suggested-accounts', daemon=True)
                self._worker.start()

    def _run(self):
        while True:
            with self._lock:
                if not self._wanted:
                    self._worker = None
                    return
                source = self._wanted.pop(0)
            try:
                if not self._due(self._rows([source]).get(source), time.time()):
                    continue
                accounts = [name.lower() for name in self.fetch(source) if isinstance(name, str)]
                self._store(source, accounts)
                log.info('Suggestions for @%s: %d accounts', source, len(accounts))
            except instagram.RateLimited as error:
                log.warning('Suggested accounts paused: %s', error)
                self._store(source, failed=True)
                with self._lock:
                    self._wanted.clear()  # later batches retry after FAILURE_DELAY
            except Exception as error:
                log.warning('Could not load suggestions for @%s: %s', source, error)
                try:
                    self._store(source, failed=True)
                except Exception:
                    log.exception('Could not record the failed suggestions for @%s', source)
            time.sleep(REQUEST_GAP)


SUGGESTIONS = SuggestionDiscovery()
