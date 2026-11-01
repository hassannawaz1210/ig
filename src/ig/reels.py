"""Reels-only pagination, separate from the photo/profile grid."""
import logging
import threading
import time
from collections import OrderedDict

from . import config, instagram

log = logging.getLogger(__name__)


class ReelCache(instagram.ProfileCache):
    STORED_KEYS = instagram.ProfileCache.STORED_KEYS + ("pending", "page_has_more")

    @property
    def path(self):
        return config.CACHE / "reels.sqlite3"


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

    def _page(self, username, state):
        previous = state.get("cursor")
        # The anonymous token is not creator-specific. Bootstrap once and
        # serialize listing requests; post-detail workers still run in parallel.
        with self.query_lock:
            if time.monotonic() - self.query_context.get("created_at", time.monotonic()) > 900:
                self.query_context.clear()
            try:
                page = instagram.reels_tab(username, after=previous, context=self.query_context)
                self.query_context.setdefault("created_at", time.monotonic())
            except instagram.InstagramError:
                # Re-bootstrap on the next scheduled attempt, respecting backoff.
                self.query_context.clear()
                raise
        seen = {p["shortcode"] for p in state["posts"]}
        pending = []
        for item in page["reels"]:
            if item["shortcode"] not in seen:
                pending.append(item)
                seen.add(item["shortcode"])
        state["pending"] = pending
        cursor = page.get("cursor")
        state["cursor"] = cursor
        # Missing/repeated cursors must not cause endless duplicate requests.
        state["page_has_more"] = bool(page["has_more"] and cursor and cursor != previous)
        state["has_more"] = bool(state["pending"] or state["page_has_more"])

    def _details(self, username, state):
        fresh = []
        for _ in range(min(self.DETAILS_PER_STEP, len(state["pending"]))):
            listing = state["pending"][0]
            code = listing["shortcode"]
            instagram.BACKOFF.check()
            try:
                data = self.media(code)
            except instagram.ProfileUnavailable:
                state["pending"].pop(0)  # Deleted/private post, not a dead creator.
                continue
            except instagram.InstagramError as error:
                # Backoff.check failures did not contact Instagram and must not
                # count against this clip. Actual failed attempts are bounded.
                listing["failures"] = listing.get("failures", 0) + 1
                if listing["failures"] >= 3:
                    log.warning("Skipping reel %s after three detail failures: %s", code, error)
                    state["pending"].pop(0)
                else:
                    state["pending"].append(state["pending"].pop(0))
                state["has_more"] = bool(state["pending"] or state["page_has_more"])
                if instagram.BACKOFF.remaining():
                    raise
                continue
            state["pending"].pop(0)
            if not (data.get("video") or data.get("video_dash_manifest")):
                continue
            item = {key: data.get(key) for key in (
                "shortcode", "owner", "caption", "audio_id", "taken_at",
                "like_count", "comment_count", "coauthors", "audio_creator", "paid_partnership")}
            item.update(shortcode=code, owner=data.get("owner") or username,
                        is_video=True, image=data.get("poster") or listing.get("image"),
                        url=listing.get("url"), play_count=listing.get("play_count"))
            state["posts"].append(item)
            fresh.append(item)
        state["has_more"] = bool(state["pending"] or state["page_has_more"])
        return fresh

    def fetch(self, username):
        state = {"username": username, "biography": "", "posts": [], "pending": [],
                 "cursor": None, "has_more": True, "page_has_more": True,
                 "fetched_at": time.time(), "lock": threading.Lock()}
        self._page(username, state)
        # Publish completed work even if a later detail request is rate limited.
        self.memory[username] = state
        try:
            self._details(username, state)
        finally:
            self.cache.save(username, state)
        return state

    def more(self, username):
        state = self.get(username)
        with state.setdefault("lock", threading.Lock()):
            try:
                if not state["pending"] and state["page_has_more"]:
                    self._page(username, state)
                fresh = self._details(username, state)
                return {"posts": fresh, "has_more": state["has_more"]}
            finally:
                self.cache.save(username, state)


REELS = ReelStore()
STATE = REELS.memory


def more_reels(username):
    return REELS.more(username)


def media_info(code):
    """Reuse post details fetched for ranking; media URLs stay in memory only."""
    return REELS.media(code)
