"""New creators from Instagram's "Accounts you might like" suggestions.

For each creator in your selected feeds, Instagram suggests similar accounts
(the row shown above a profile's posts). These are the main source of new
creators: Instagram has already judged them similar, unlike accounts that
merely appear in captions. An account suggested by several of your creators
ranks higher, and it belongs to the feed of the creator that suggested it.

Suggestions are fetched in the background, a few seconds apart, cached in
SQLite for a day, and loading reels never waits for them.
"""
import json
import logging
import random
import threading
import time

from . import config, instagram, recommendations

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
        """Suggested accounts for the creators in these feeds, as
        {username: {'source': 'suggested', 'via': creator, 'feed': feed, 'count': n}}.
        Missing or stale lists are queued for the background worker."""
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
        except Exception:
            log.exception('Could not read suggested accounts; continuing without them')
            return {}
        self._schedule(sources, rows)
        found = {}
        for source in sources:
            for name in (rows.get(source) or (0, [], None))[1]:
                if name in exclude or not config.USER_RE.fullmatch(name):
                    continue
                if name in found:
                    found[name]['count'] += 1
                else:
                    found[name] = {'source': 'suggested', 'via': source, 'feed': owners[source], 'count': 1}
        return found

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
