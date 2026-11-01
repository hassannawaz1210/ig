"""Bounded background reels loading, independent of reel delivery."""
import time
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
        for _, name, paging in candidates:
            if len(self.pending) >= 2:
                break
            self.last_scheduled[name] = time.monotonic()
            self.pending[name] = (self.executor.submit(self._load, name, paging), pool)

    def waiting(self, names):
        return bool(set(names) & (self.pending.keys() | {n for n, deadline in self.retry_at.items()
                                                        if deadline > time.monotonic()}))


REFILL = Refill()
