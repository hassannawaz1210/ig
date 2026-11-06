"""Bounded background reels loading, independent of reel delivery."""
import time
import random
from concurrent.futures import ThreadPoolExecutor
from .instagram import RateLimited, ProfileUnavailable, BACKOFF
from .reels import REELS, STATE, more_reels
from .topics import TOPIC_TRIAL_SHARE


class Refill:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='ig-refill')
        self.pending = {}
        self.retry_at = {}
        self.last_scheduled = {}
        self.completed_seconds = {}
        self.load_seconds = 12.0
        self.next_discovery_at = 0.0
        self.last_exploration = None
        self.exploration_pending = set()
        self.refresh_after = {}
        self.retained_refresh = set()

    def _load(self, name, paging):
        started = time.monotonic()
        try:
            if paging == "explore":
                return REELS.explore(name)
            return (more_reels if paging else REELS.load)(name)
        finally:
            self.completed_seconds[name] = time.monotonic() - started


    def collect(self, pool, on_error):
        # Called under the batch lock; workers never modify feed pools.
        for name, (future, owner_pool) in list(self.pending.items()):
            if not future.done():
                continue
            del self.pending[name]
            self.exploration_pending.discard(name)
            try:
                future.result()
                elapsed = self.completed_seconds.get(name)
                if elapsed is not None:
                    # Rise promptly when Instagram slows; decay more gradually.
                    elapsed = min(60, max(1, elapsed))
                    weight = .5 if elapsed > self.load_seconds else .15
                    self.load_seconds += weight * (elapsed - self.load_seconds)
            except RateLimited:
                self.retry_at[name] = time.monotonic() + max(3, BACKOFF.remaining())
            except ProfileUnavailable:
                owner_pool['dead'].add(name)
            except Exception as error:
                self.retry_at[name] = time.monotonic() + 30
                if name not in self.retained_refresh:
                    on_error(owner_pool, name, error)
            finally:
                self.completed_seconds.pop(name, None)
                self.retained_refresh.discard(name)

    def schedule(self, pool, names, saved, *, states=None):
        """Page sources below twelve unseen clips, at most two source workers at once."""
        states = STATE if states is None else states
        now = time.monotonic()
        pause = BACKOFF.remaining()
        if pause > 0:
            for name in names:
                self.retry_at[name] = max(self.retry_at.get(name, 0), now + pause)
            return
        trusted = set(saved) | pool.get('accepted', set())
        candidates, older = [], []
        usable = set()
        ready = pool.get('buffered', 0)
        for name in dict.fromkeys(names):
            state = states.get(name)
            if state is None:
                state = REELS.cached(name)  # disk only; never network
            unseen = sum(p.get('is_video', False) and p['shortcode'] not in pool['served']
                         for p in state['posts']) if state else 0
            if unseen:
                usable.add(name)
            if name in trusted:
                ready += unseen
            if name in self.pending or self.retry_at.get(name, 0) > now:
                continue
            if state is None:
                candidates.append((0, name, False))
            elif REELS.is_stale(state) and now >= self.refresh_after.get(name, 0):
                # Even a full archive needs its latest page refreshed. Failed or
                # stale-cache refreshes must not repeat on every batch request.
                candidates.append((0 if unseen == 0 else 2, name, False))
            elif (name in trusted or not state['posts'] or
                  (len(state['posts']) < 9 and state.get('pending'))) and state.get('has_more'):
                if unseen < 12:
                    retry_delay = (state.get('archive_retry_at') or 0) - time.time()
                    if 'pending' in state and not state['pending'] and retry_delay > 0:
                        self.retry_at[name] = now + retry_delay
                    elif ('pending' not in state or state['pending'] or
                          REELS.can_explore(state)):
                        candidates.append((0 if unseen == 0 else 1, name, True))
            if state and name in trusted and (REELS.can_explore(state) or any(
                    p.get('reel_age_group') == 'older' for p in state.get('pending', []))):
                older.append((3, name, 'explore'))
        random.shuffle(candidates)
        candidates.sort(key=lambda entry: (entry[0], self.last_scheduled.get(entry[1], 0)))
        trials = [c for c in candidates if c[1] not in saved]
        regular = [c for c in candidates if c[1] in saved]
        # Older work can replace a buffered source's normal detail hydration;
        # do not wait until all recent clips have been consumed. Cold/empty
        # sources and latest-page refreshes still take priority.
        urgent_names = {c[1] for c in candidates if c[0] == 0 or c[2] is False}
        older = [c for c in older if c[1] not in urgent_names] if ready >= 6 else []
        random.shuffle(older)
        older.sort(key=lambda c: self.last_scheduled.get(c[1], 0))
        special = None
        kind = None
        if not self.exploration_pending and now >= self.next_discovery_at:
            # Alternate the single exploration allowance: older clips must not
            # crowd out new creators, nor add another stream of requests.
            if older and (not trials or self.last_exploration == 'discovery'):
                special, kind = older[0], 'older'
            elif trials:
                weights = pool.get('discovery_weights', {})
                strong = [c for c in trials if weights.get(c[1], 0) >= 1]
                # Topic-page creators get a fixed share (see topics.py), or
                # hundreds of related leads would nearly always be loaded first.
                from_topics = [c for c in trials if c[1] in pool.get('topic_leads', ())]
                if from_topics and random.random() < TOPIC_TRIAL_SHARE:
                    trials = from_topics
                elif strong and random.random() < .8:
                    trials = strong
                oldest = min(self.last_scheduled.get(c[1], 0) for c in trials)
                eligible = [c for c in trials if self.last_scheduled.get(c[1], 0) == oldest]
                special = random.choices(eligible, weights=[weights.get(c[1], 1) for c in eligible], k=1)[0]
                kind = 'discovery'
        elif now < self.next_discovery_at:
            for _, name, _ in trials:
                self.retry_at[name] = self.next_discovery_at
        candidates = regular
        if special:
            regular = [c for c in regular if c[1] != special[1]]
            # Startup and empty sources get both workers before optional work.
            urgent = [c for c in regular if c[0] == 0]
            rest = [c for c in regular if c[0] != 0]
            candidates = urgent + [special] + rest if ready >= 6 else regular + [special]
        for _, name, paging in candidates:
            if len(self.pending) >= 2:
                break
            if special and name == special[1]:
                self.next_discovery_at = now + 30
                self.last_exploration = kind
                self.exploration_pending.add(name)
            if paging is False:
                self.refresh_after[name] = now + 300
                if name in usable:
                    self.retained_refresh.add(name)
            self.last_scheduled[name] = now
            self.pending[name] = (self.executor.submit(self._load, name, paging), pool)

    def waiting(self, names):
        return bool(set(names) & (self.pending.keys() | {n for n, deadline in self.retry_at.items()
                                                        if deadline > time.monotonic()}))


REFILL = Refill()
