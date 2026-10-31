"""Logged-out Instagram page parsing, requests, pagination, and profile caching."""
import json
import logging
import random
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from urllib.parse import urlencode
from curl_cffi import requests as cr
from . import config
from .migrations import migrate

log = logging.getLogger(__name__)


POSTS_DOC_ID = "28904005159232689"


POSTS_FRIENDLY_NAME = "PolarisLoggedOutDesktopWWWProfilePostsTabContentQuery_connection"


COMMENTS_DOC_ID = "27261273046856309"


COMMENTS_FRIENDLY_NAME = "PolarisLoggedOutDesktopWWWPostCommentsPaginationQuery"


REQUEST_TIMEOUT = 30          # seconds for one Instagram request
PAGE_SIZE = 50                # posts per pagination request
PAGINATION_PAUSE = (1.0, 2.2) # random wait in seconds; stays under the hourly cap
FIRST_BACKOFF = 30.0          # seconds to pause after the first rate limit
LONGEST_BACKOFF = 600.0       # the pause doubles up to this many seconds
# Fixed IDs Instagram's own web app sends with GraphQL requests.
WEB_APP_ID = "936619743392459"
ASBD_ID = "359341"


UA_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class InstagramError(Exception):
    """A failure talking to Instagram. The message is safe to show users."""


class RateLimited(InstagramError):
    """Instagram is limiting logged-out requests; waiting usually fixes it."""


class ProfileUnavailable(InstagramError):
    """The profile does not exist, is private, or has no public posts."""


class UnexpectedResponse(InstagramError):
    """Instagram answered, but not in a format this app understands."""


class NetworkError(InstagramError):
    """Instagram could not be reached."""


class Backoff:
    """Shared pause after a rate limit, so every thread stops asking at once.

    Each consecutive limit doubles the pause (30 s up to 10 min); any normal
    response resets it."""

    def __init__(self, first=FIRST_BACKOFF, longest=LONGEST_BACKOFF):
        self.first, self.longest = first, longest
        self.until = 0.0
        self.strikes = 0
        self._lock = threading.Lock()

    def remaining(self) -> float:
        with self._lock:
            return max(0.0, self.until - time.time())

    def check(self) -> None:
        wait = self.remaining()
        if wait > 0:
            raise RateLimited(f"Instagram is rate limiting requests; retrying in {int(wait) + 1} s")

    def hit(self) -> float:
        with self._lock:
            delay = min(self.longest, self.first * 2 ** self.strikes)
            self.strikes += 1
            self.until = time.time() + delay
        log.warning("Instagram rate limit; pausing requests for %d s (strike %d)", delay, self.strikes)
        return delay

    def reset(self) -> None:
        with self._lock:
            if self.strikes:
                log.info("Instagram requests are working again")
            self.strikes = 0
            self.until = 0.0


BACKOFF = Backoff()


def _new_session():
    return cr.Session(impersonate="chrome", headers=UA_HEADERS)


def request(session, method: str, url: str, **kwargs):
    """One Instagram request with shared rate-limit handling."""
    BACKOFF.check()
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    try:
        response = session.request(method, url, **kwargs)
    except cr.exceptions.RequestException as error:
        log.warning("Instagram request failed: %s %s: %s", method, url, error)
        raise NetworkError("Could not reach Instagram; check your connection") from error
    final_url = str(getattr(response, "url", "") or "")
    if response.status_code == 429:
        BACKOFF.hit()
        raise RateLimited("Instagram is rate limiting requests; wait a few minutes")
    if "/accounts/login" in final_url or "/challenge" in final_url:
        BACKOFF.hit()
        raise RateLimited("Instagram is asking to log in (a logged-out limit); wait a few minutes")
    if response.status_code < 400:
        BACKOFF.reset()
    log.debug("%s %s -> %s", method, url, response.status_code)
    return response


def sjs_payloads(html: str, needle: str):
    for m in re.finditer(
        r'<script[^>]*type="application/json"[^>]*data-sjs[^>]*>(.*?)</script>',
        html, re.DOTALL,
    ):
        blob = m.group(1)
        if needle not in blob:
            continue
        try:
            yield json.loads(blob)
        except json.JSONDecodeError:
            continue


def nested_find(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            hit = nested_find(v, key)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = nested_find(v, key)
            if hit is not None:
                return hit
    return None


def audio_id(media):
    clips = media.get('clips_metadata') or {}
    music = clips.get('music_info') or {}
    asset = music.get('music_asset_info') or {}
    original = clips.get('original_sound_info') or {}
    return str(asset.get('audio_cluster_id') or asset.get('id') or
               original.get('audio_asset_id') or original.get('id') or '') or None


def slim(n: dict) -> dict:
    img = n.get("display_uri")
    if not img:
        cands = (n.get("image_versions2") or {}).get("candidates") or []
        img = (cands[0] or {}).get("url") if cands else None
    return {
        "shortcode": n.get("code"),
        "audio_id": audio_id(n),
        "url": f"https://www.instagram.com/p/{n.get('code')}/",
        "owner": (n.get("user") or {}).get("username"),
        "taken_at": n.get("taken_at"),
        "media_type": n.get("media_type"),
        "is_video": n.get("media_type") == 2 or n.get("product_type") == "clips",
        "is_carousel": n.get("media_type") == 8
                       or (n.get("carousel_media_count") or 0) > 1,
        "carousel_count": n.get("carousel_media_count"),
        "like_count": n.get("like_count"),
        "comment_count": n.get("comment_count"),
        "caption": (n.get("caption") or {}).get("text"),
        "image": img,
    }


def feed(username: str) -> list[dict]:
    """Full archive in one go (used by `ig feed`; slow on big accounts)."""
    state = init_state(username)
    while state.get("has_more"):
        if not more_posts(username).get("posts"):
            break
    return state["posts"]


def _post_page(code: str) -> str:
    response = request(_new_session(), "GET", f"https://www.instagram.com/p/{code}/")
    if response.status_code == 404:
        raise ProfileUnavailable(f"Post {code} is unavailable")
    if response.status_code >= 400:
        raise UnexpectedResponse(f"Instagram returned HTTP {response.status_code} for post {code}")
    return response.text


def _post_media(code: str) -> dict:
    for payload in sjs_payloads(_post_page(code), "xig_polaris_media"):
        media = nested_find(payload, "xig_polaris_media")
        if media:
            return media.get("if_not_gated_logged_out") or media
    raise UnexpectedResponse(f"Instagram did not include post data for {code}; try again later")


def media_info(code: str) -> dict:
    """Full data for one post incl. direct MP4 URL (reels), via the
    post page's xig_polaris_media payload, including audio and tagged creators."""
    m = _post_media(code)

    def best(vlist):
        # Renditions sometimes arrive without dimensions; the first entry then
        # matches what every other entry would resolve to anyway.
        return (max(vlist, key=lambda x: (x.get("width") or 0) * (x.get("height") or 0)).get("url")
                if vlist else None)

    out = {
        "shortcode": m.get("code"),
        "audio_id": audio_id(m),
        "owner": (m.get("user") or {}).get("username"),
        "like_count": m.get("like_count"),
        "comment_count": m.get("comment_count"),
        "taken_at": m.get("taken_at"),
        "caption": (m.get("caption") or {}).get("text"),
        "video": best(m.get("video_versions") or []),
        # The manifest holds higher-quality renditions than the progressive
        # URL; the server swaps the video URL for a locally assembled stream.
        "video_dash_manifest": m.get("video_dash_manifest"),
        "poster": best((m.get("image_versions2") or {}).get("candidates") or []),
        "slides": [],
    }
    # accounts to feed the reels discovery pool
    tagged = []
    for ut in ((m.get("usertags") or {}).get("users") or []):
        u = ((ut or {}).get("user") or {}).get("username")
        if u:
            tagged.append(u)
    tagged += re.findall(r"@([A-Za-z0-9._]{3,30})",
                         (m.get("caption") or {}).get("text") or "")
    out["tagged_users"] = sorted(set(tagged))[:8]

    for s in m.get("carousel_media") or []:
        out["slides"].append({
            "video": best(s.get("video_versions") or []),
            "image": best((s.get("image_versions2") or {}).get("candidates") or []),
        })
    return out


def post_comments(code: str, first: int = 20) -> dict:
    """Top-level comments of a post, without login.

    Comments are not embedded in logged-out post pages; a separate GraphQL
    query on the post page's media id returns them. One page load seeds the
    anonymous session (cookies and tokens), then the query runs on it."""
    session = _new_session()
    html = _post_page(code)
    if '"LSD"' not in html:
        raise UnexpectedResponse(f"Instagram did not include tokens for {code}; try again later")
    media = None
    for payload in sjs_payloads(html, "xig_polaris_media"):
        found = nested_find(payload, "xig_polaris_media")
        if found:
            media = found.get("if_not_gated_logged_out") or found
            break
    pk = (media or {}).get("pk") or (media or {}).get("id")
    if not pk:
        raise UnexpectedResponse(f"Instagram did not include the media id for {code}")
    owner = ((media or {}).get("user") or {}).get("username")
    lsd_m = re.search(r'"LSD",\[\],\{"token":"([\w-]+)"', html)
    lsd = lsd_m.group(1) if lsd_m else ""
    csrf = session.cookies.get("csrftoken", "")
    variables = json.dumps({"after": None, "first": first, "media_id": str(pk)},
                           separators=(",", ":"))
    body = urlencode({
        "av": "0", "__d": "www", "__user": "0", "__a": "1",
        "__comet_req": "7", "lsd": lsd,
        "jazoest": "2" + str(sum(ord(c) for c in lsd)),
        "fb_api_caller_class": "RelayModern",
        "fb_api_req_friendly_name": COMMENTS_FRIENDLY_NAME,
        "server_timestamps": "true", "variables": variables,
        "doc_id": COMMENTS_DOC_ID,
    })
    r = request(session, "POST", "https://www.instagram.com/api/graphql", data=body,
                headers={
                    "content-type": "application/x-www-form-urlencoded",
                    "x-fb-friendly-name": COMMENTS_FRIENDLY_NAME,
                    "x-fb-lsd": lsd, "x-csrftoken": csrf,
                    "x-ig-app-id": WEB_APP_ID, "x-asbd-id": ASBD_ID,
                    "origin": "https://www.instagram.com",
                    "referer": f"https://www.instagram.com/p/{code}/",
                })
    try:
        payload = r.json()
    except ValueError:
        BACKOFF.hit()
        raise RateLimited("Instagram blocked loading comments; wait a few minutes") from None
    if payload.get("require_login") or payload.get("status") == "fail":
        BACKOFF.hit()
        raise RateLimited("Instagram is limiting logged-out browsing; wait a few minutes")
    connection = ((payload.get("data") or {}).get("xig_polaris_media") or {}) \
        .get("comments_connection")
    if connection is None:
        raise UnexpectedResponse(f"Instagram sent no comment data for {code}")
    comments = []
    for edge in connection.get("edges") or []:
        node = edge.get("node") or {}
        user = node.get("user") or {}
        username = user.get("username")
        if not username:
            continue
        comments.append({
            "username": username,
            "text": node.get("text") or "",
            "like_count": node.get("comment_like_count") or 0,
            "created_at": node.get("created_at"),
            "is_owner": username == owner,
        })
    return {"shortcode": media.get("code") or code, "owner": owner, "comments": comments}


def fetch_profile_page(username: str):
    """GET the profile page; return (session, html)."""
    s = _new_session()
    r = request(s, "GET", f"https://www.instagram.com/{username}/")
    if r.status_code == 404:
        raise ProfileUnavailable(f"@{username} does not exist or was removed")
    if r.status_code >= 400:
        raise UnexpectedResponse(f"Instagram returned HTTP {r.status_code} for @{username}")
    return s, r.text


def parse_profile(html: str, username: str = ""):
    """Extract (user, first_connection, lsd) from profile page HTML."""
    lsd_m = re.search(r'"LSD",\[\],\{"token":"([\w-]+)"', html)
    user = conn = None
    for payload in sjs_payloads(html, "polaris_ordered_timeline_connection"):
        user = user or nested_find(payload, "xig_user_by_username")
        conn = nested_find(payload, "polaris_ordered_timeline_connection")
        if conn and conn.get("edges"):
            break
    if not conn:
        name = f"@{username}" if username else "This profile"
        for payload in sjs_payloads(html, "xig_user_by_username"):
            found = nested_find(payload, "xig_user_by_username")
            if isinstance(found, dict) and found.get("is_private"):
                raise ProfileUnavailable(f"{name} is private")
        raise UnexpectedResponse(f"Instagram did not include posts for {name}; "
                                 "it may be rate limiting, so try again later")
    return user, conn, (lsd_m.group(1) if lsd_m else "")


def profile(username: str) -> tuple[dict, list[str]]:
    _, html = fetch_profile_page(username)
    if "xig_user_by_username" not in html:
        raise UnexpectedResponse(f"Instagram did not include profile data for @{username}; try again later")
    user = None
    for payload in sjs_payloads(html, "xig_user_by_username"):
        user = nested_find(payload, "xig_user_by_username")
        if user and "follower_count" in user:
            break
    codes = [c for c in dict.fromkeys(
        re.findall(r'"code":"([A-Za-z0-9_-]{10,12})"', html))]
    return user, codes


def post(url_or_code: str) -> dict:
    return _post_media(url_or_code.rstrip("/").rsplit("/", 1)[-1])


class ProfileCache:
    """Profiles saved on disk, so restarts do not refetch every account.

    Only public metadata is stored: bio, slim posts and the pagination cursor.
    Sessions and tokens stay in memory. Fresh entries are used directly; older
    ones are only a fallback while Instagram is rate limiting."""

    DATABASE_TIMEOUT = 10  # seconds to wait for another process's write
    FRESH_FOR = 60 * 60
    FALLBACK_FOR = 24 * 60 * 60
    KEEP_FOR = 7 * 24 * 60 * 60
    MAX_PROFILES = 1000
    STORED_KEYS = ("user_id", "username", "biography", "posts", "cursor", "has_more", "fetched_at")

    @property
    def path(self):
        return config.CACHE / "profiles.sqlite3"

    @staticmethod
    def _initial_schema(connection):
        connection.execute("CREATE TABLE IF NOT EXISTS profiles "
                           "(username TEXT PRIMARY KEY, data TEXT NOT NULL, fetched REAL NOT NULL)")

    @contextmanager
    def _database(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=self.DATABASE_TIMEOUT)
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                migrate(connection, (self._initial_schema,))
                yield connection
        finally:
            connection.close()

    def load(self, username: str, max_age: float) -> dict | None:
        if not self.path.exists():
            return None
        try:
            with self._database() as connection:
                row = connection.execute("SELECT data FROM profiles WHERE username = ? AND fetched > ?",
                                         (username, time.time() - max_age)).fetchone()
            if not row:
                return None
            data = json.loads(row[0])
        except (sqlite3.Error, json.JSONDecodeError, RuntimeError):
            log.exception("Could not read the cached profile for @%s; fetching it again", username)
            return None
        data["lock"] = threading.Lock()
        return data

    def fresh_usernames(self) -> set[str]:
        if not self.path.exists():
            return set()
        try:
            with self._database() as connection:
                return {row[0] for row in connection.execute(
                    "SELECT username FROM profiles WHERE fetched > ?", (time.time() - self.FRESH_FOR,))}
        except (sqlite3.Error, RuntimeError):
            log.exception("Could not read the profile cache")
            return set()

    def save(self, username: str, state: dict) -> None:
        data = {key: state.get(key) for key in self.STORED_KEYS}
        try:
            with self._database() as connection:
                connection.execute("INSERT OR REPLACE INTO profiles VALUES (?, ?, ?)",
                                   (username, json.dumps(data), state.get("fetched_at") or time.time()))
                connection.execute("DELETE FROM profiles WHERE fetched < ?", (time.time() - self.KEEP_FOR,))
                connection.execute("DELETE FROM profiles WHERE username NOT IN "
                                   "(SELECT username FROM profiles ORDER BY fetched DESC LIMIT ?)",
                                   (self.MAX_PROFILES,))
        except (sqlite3.Error, RuntimeError):
            # The cache only saves time; browsing continues with memory state.
            log.exception("Could not save @%s to the profile cache", username)


class ProfileStore:
    """Profiles in memory, then on disk, then from Instagram."""

    def __init__(self, cache: ProfileCache):
        self.memory: dict[str, dict] = {}
        self.cache = cache

    def is_stale(self, state: dict) -> bool:
        # States without a fetch time (built in tests or by hand) count as current.
        fetched = state.get("fetched_at")
        return fetched is not None and time.time() - fetched > self.cache.FRESH_FOR

    def cached(self, username: str) -> dict | None:
        """A current profile without any network request, or None."""
        state = self.memory.get(username)
        if state is not None and not self.is_stale(state):
            return state
        state = self.cache.load(username, self.cache.FRESH_FOR)
        if state is not None:
            log.debug("Using cached profile for @%s", username)
            self.memory[username] = state
        return state

    def fetch(self, username: str) -> dict:
        """Always download the profile's first page."""
        s, html = fetch_profile_page(username)
        user, conn, lsd = parse_profile(html, username)
        user = user or {}
        state = {
            "session": s, "lsd": lsd,
            "csrf": s.cookies.get("csrftoken", ""),
            "user_id": user.get("id"), "username": user.get("username", username),
            "biography": user.get("biography") or "",
            "posts": [slim(e["node"]) for e in conn.get("edges") or []],
            "cursor": (conn.get("page_info") or {}).get("end_cursor"),
            "has_more": (conn.get("page_info") or {}).get("has_next_page", False),
            "fetched_at": time.time(),
            "lock": threading.Lock(),
        }
        if not state["user_id"]:
            state["has_more"] = False  # Pagination needs the numeric account ID.
        self.memory[username] = state
        self.cache.save(username, state)
        log.info("Fetched @%s (%d posts)", username, len(state["posts"]))
        return state

    def load(self, username: str) -> tuple[dict, bool]:
        """Return (state, used_network). Falls back to older copies while rate limited."""
        state = self.cached(username)
        if state is not None:
            return state, False
        try:
            return self.fetch(username), True
        except RateLimited:
            older = self.memory.get(username) or self.cache.load(username, self.cache.FALLBACK_FOR)
            if older is None:
                raise
            log.warning("Rate limited; showing an older copy of @%s", username)
            self.memory[username] = older
            return older, True

    def get(self, username: str) -> dict:
        return self.load(username)[0]

    def _ensure_session(self, username: str, state: dict) -> None:
        """Cached profiles have no session; one page load restores the token."""
        if state.get("session") is not None:
            return
        s, html = fetch_profile_page(username)
        _, _, lsd = parse_profile(html, username)
        state.update(session=s, lsd=lsd, csrf=s.cookies.get("csrftoken", ""))

    def more(self, username: str) -> dict:
        """One pagination step (~50 posts). Called by the Load-more button."""
        state = self.get(username)
        with state.setdefault("lock", threading.Lock()):
            if not state.get("has_more") or not state.get("cursor"):
                return {"posts": [], "has_more": False}
            self._ensure_session(username, state)
            time.sleep(random.uniform(*PAGINATION_PAUSE))
            lsd = state["lsd"]
            variables = json.dumps({
                "after": state["cursor"], "first": PAGE_SIZE, "id": state["user_id"],
                "__relay_internal__pv__PolarisSEOIsCrawlerrelayprovider": False,
            }, separators=(",", ":"))
            body = urlencode({
                "av": "0", "__d": "www", "__user": "0", "__a": "1",
                "__comet_req": "7", "lsd": lsd,
                "jazoest": "2" + str(sum(ord(c) for c in lsd)),
                "fb_api_caller_class": "RelayModern",
                "fb_api_req_friendly_name": POSTS_FRIENDLY_NAME,
                "server_timestamps": "true", "variables": variables,
                "doc_id": POSTS_DOC_ID,
            })
            r = request(state["session"], "POST", "https://www.instagram.com/api/graphql", data=body,
                        headers={
                            "content-type": "application/x-www-form-urlencoded",
                            "x-fb-friendly-name": POSTS_FRIENDLY_NAME,
                            "x-fb-lsd": lsd, "x-csrftoken": state["csrf"],
                            "x-ig-app-id": WEB_APP_ID, "x-asbd-id": ASBD_ID,
                            "origin": "https://www.instagram.com",
                            "referer": f"https://www.instagram.com/{username}/",
                        })
            try:
                payload = r.json()
            except ValueError:
                BACKOFF.hit()
                raise RateLimited("Instagram blocked loading more posts; wait a few minutes") from None
            if not isinstance(payload, dict):
                raise UnexpectedResponse("Instagram sent an unexpected reply while loading more posts")
            if payload.get("require_login") or payload.get("status") == "fail":
                BACKOFF.hit()
                raise RateLimited("Instagram is limiting logged-out browsing; wait a few minutes")
            c = ((payload.get("data") or {}).get("node") or {}) \
                .get("polaris_ordered_timeline_connection") or {}
            edges = c.get("edges") or []
            if not edges:
                log.warning("Pagination for @%s returned no posts; POSTS_DOC_ID may be stale", username)
                raise UnexpectedResponse("Instagram returned no more posts. Try again later; if this "
                                         "keeps happening, the pagination query ID needs updating")
            fresh = [slim(e["node"]) for e in edges]
            state["posts"] += fresh
            pi = c.get("page_info") or {}
            state["cursor"] = pi.get("end_cursor")
            state["has_more"] = bool(pi.get("has_next_page"))
            self.cache.save(username, state)
            return {"posts": fresh, "has_more": state["has_more"],
                    "total": len(state["posts"])}


PROFILES = ProfileStore(ProfileCache())
STATE: dict[str, dict] = PROFILES.memory


def init_state(username: str) -> dict:
    """Fresh session; seed first 12 posts. Always downloads the profile."""
    return PROFILES.fetch(username)


def load_profile(username: str) -> dict:
    """Memory, then the on-disk cache, then Instagram."""
    return PROFILES.get(username)


def more_posts(username: str) -> dict:
    return PROFILES.more(username)
