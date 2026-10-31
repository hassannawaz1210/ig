"""Bounded background profile loading, independent of reel delivery."""
import time
from concurrent.futures import ThreadPoolExecutor
from .instagram import PROFILES, STATE, more_posts, RateLimited, ProfileUnavailable, BACKOFF


class Refill:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='ig-refill')
        self.pending = {}
        self.retry_at = {}

    def collect(self, pool, on_error):
        # Called under the batch lock; workers never modify feed pools.
        for name, (future, owner_pool) in list(self.pending.items()):
            if not future.done():
                continue
            del self.pending[name]
            try:
                future.result()
            except RateLimited:
                self.retry_at[name] = time.monotonic() + max(3, BACKOFF.remaining())
            except ProfileUnavailable:
                owner_pool['dead'].add(name)
            except Exception as error:
                self.retry_at[name] = time.monotonic() + 30
                on_error(owner_pool, name, error)

    def schedule(self, pool, names, saved, *, states=None):
        """Page sources below twelve unseen clips, at most two requests at once."""
        states = STATE if states is None else states
        candidates = []
        for name in names:
            if name in self.pending or self.retry_at.get(name, 0) > time.monotonic():
                continue
            state = states.get(name)
            if state is None:
                state = PROFILES.cached(name)  # disk only; never network
            if state is None:
                candidates.append((0 if name in saved else 2, name, False))
            elif (name in saved or name in pool.get('accepted', set())) and state.get('has_more'):
                unseen = sum(p.get('is_video') and p['shortcode'] not in pool['served'] for p in state['posts'])
                if unseen < 12:
                    candidates.append((0 if unseen == 0 else 1, name, True))
        # One discovery source may run alongside saved-source replenishment.
        candidates.sort(key=lambda entry: entry[0])
        for _, name, paging in candidates:
            if len(self.pending) >= 2:
                break
            self.pending[name] = (self.executor.submit(more_posts if paging else PROFILES.load, name), pool)

    def waiting(self, names):
        return bool(set(names) & (self.pending.keys() | {n for n, deadline in self.retry_at.items()
                                                        if deadline > time.monotonic()}))


REFILL = Refill()
