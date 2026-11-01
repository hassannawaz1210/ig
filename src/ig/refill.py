"""Bounded background reels loading, independent of reel delivery."""
import time
import random
from concurrent.futures import ThreadPoolExecutor
from .instagram import RateLimited, ProfileUnavailable, BACKOFF
from .reels import REELS, STATE, more_reels


class Refill:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='ig-refill')
        self.pending = {}
        self.retry_at = {}
        self.last_scheduled = {}
        self.completed_seconds = {}
        self.load_seconds = 12.0
        self.next_discovery_at = 0.0

    def _load(self, name, paging):
        started = time.monotonic()
        try:
            return (more_reels if paging else REELS.load)(name)
        finally:
            self.completed_seconds[name] = time.monotonic() - started


    def collect(self, pool, on_error):
        # Called under the batch lock; workers never modify feed pools.
        for name, (future, owner_pool) in list(self.pending.items()):
            if not future.done():
                continue
            del self.pending[name]
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
                on_error(owner_pool, name, error)
            finally:
                self.completed_seconds.pop(name, None)

    def schedule(self, pool, names, saved, *, states=None):
        """Page sources below twelve unseen clips, at most two source workers at once."""
        states = STATE if states is None else states
        candidates = []
        for name in names:
            if name in self.pending or self.retry_at.get(name, 0) > time.monotonic():
                continue
            state = states.get(name)
            if state is None:
                state = REELS.cached(name)  # disk only; never network
            if state is None:
                candidates.append((0 if name in saved else 2, name, False))
            elif REELS.is_stale(state) and not any(
                    p.get('is_video') and p['shortcode'] not in pool['served'] for p in state['posts']):
                candidates.append((0 if name in saved else 2, name, False))
            elif (name in saved or name in pool.get('accepted', set()) or not state['posts']) and state.get('has_more'):
                unseen = sum(p.get('is_video') and p['shortcode'] not in pool['served'] for p in state['posts'])
                if unseen < 12:
                    candidates.append((0 if unseen == 0 else 1, name, True))
        # One discovery source may run alongside saved-source replenishment.
        candidates.sort(key=lambda entry: (entry[0], self.last_scheduled.get(entry[1], 0)))
        # When the queue is healthy, reserve one of the existing two workers
        # for discovery. A fixed request cadence bounds background exploration.
        ready = pool.get('buffered', 0) + sum(
            p.get('is_video', False) and p['shortcode'] not in pool['served']
            for name, state in states.items() if name in saved for p in state['posts'])
        trials = [c for c in candidates if c[1] not in saved]
        saved_candidates = [c for c in candidates if c[1] in saved]
        trial_running = any(name not in saved for name in self.pending)
        if trials and not trial_running and time.monotonic() >= self.next_discovery_at:
            weights = pool.get('discovery_weights', {})
            # Try never-inspected candidates before revisiting old sources.
            oldest = min(self.last_scheduled.get(c[1], 0) for c in trials)
            eligible = [c for c in trials if self.last_scheduled.get(c[1], 0) == oldest]
            trial = random.choices(eligible, weights=[weights.get(c[1], 1) for c in eligible], k=1)[0]
            if ready >= 6 or not saved_candidates:
                candidates = [trial] + saved_candidates
            else:
                candidates = saved_candidates + [trial]
        else:
            if trials and time.monotonic() < self.next_discovery_at:
                for _, name, _ in trials:
                    self.retry_at[name] = self.next_discovery_at
            candidates = saved_candidates
        for _, name, paging in candidates:
            if len(self.pending) >= 2:
                break
            if name not in saved:
                if trial_running or time.monotonic() < self.next_discovery_at:
                    continue
                self.next_discovery_at = time.monotonic() + 30
                trial_running = True
            self.last_scheduled[name] = time.monotonic()
            self.pending[name] = (self.executor.submit(self._load, name, paging), pool)

    def waiting(self, names):
        return bool(set(names) & (self.pending.keys() | {n for n, deadline in self.retry_at.items()
                                                        if deadline > time.monotonic()}))


REFILL = Refill()
