"""Logged-out Instagram page parsing, requests, pagination, and profile caching."""
import json
import logging
import random
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from collections import OrderedDict
from copy import deepcopy
from urllib.parse import urlencode, urlsplit
from curl_cffi import requests as cr
from . import config
from .migrations import migrate

log = logging.getLogger(__name__)


POSTS_DOC_ID = "28904005159232689"


POSTS_FRIENDLY_NAME = "PolarisLoggedOutDesktopWWWProfilePostsTabContentQuery_connection"


COMMENTS_DOC_ID = "27261273046856309"


COMMENTS_FRIENDLY_NAME = "PolarisLoggedOutDesktopWWWPostCommentsPaginationQuery"


REELS_DOC_ID = "27838951732404191"


REELS_FRIENDLY_NAME = "PolarisLoggedOutDesktopWWWProfileReelsTabContentQuery"


AYML_DOC_ID = "27840486182295820"


AYML_FRIENDLY_NAME = "PolarisLoggedOutDesktopWWWAYMLQuery"


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
    def object_field(obj, key):
        value = obj.get(key)
        return value if isinstance(value, dict) else {}
    clips = object_field(media, 'clips_metadata')
    music = object_field(clips, 'music_info')
    asset = object_field(music, 'music_asset_info')
    original = object_field(clips, 'original_sound_info')
    return str(asset.get('audio_cluster_id') or asset.get('id') or
               original.get('audio_asset_id') or original.get('id') or '') or None


def _preview_image(n: dict):
    img = n.get("display_uri")
    if not img:
        cands = (n.get("image_versions2") or {}).get("candidates") or []
        img = (cands[0] or {}).get("url") if cands else None
    return img


def slim(n: dict) -> dict:
    img = _preview_image(n)
    if not img:
        # Older multi-image posts may carry images only on their items.
        items = n.get("carousel_media") or []
        img = _preview_image(items[0]) if items and isinstance(items[0], dict) else None
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


_COMMENT_CONTEXTS = OrderedDict()
_COMMENT_PAGES = OrderedDict()
_COMMENT_LOCK = threading.RLock()
_COMMENT_FETCH_LOCK = threading.Lock()
COMMENT_TTL = 900


def _post_page(code: str, session=None) -> str:
    session = session if session is not None else _new_session()
    response = request(session, "GET", f"https://www.instagram.com/p/{code}/")
    if response.status_code == 404:
        raise ProfileUnavailable(f"Post {code} is unavailable")
    if response.status_code >= 400:
        raise UnexpectedResponse(f"Instagram returned HTTP {response.status_code} for post {code}")
    with _COMMENT_LOCK:
        _COMMENT_CONTEXTS[code] = (time.monotonic(), session, response.text)
        _COMMENT_CONTEXTS.move_to_end(code)
        while len(_COMMENT_CONTEXTS) > 64:
            _COMMENT_CONTEXTS.popitem(last=False)
    return response.text


def _post_media(code: str) -> dict:
    for payload in sjs_payloads(_post_page(code), "xig_polaris_media"):
        media = nested_find(payload, "xig_polaris_media")
        if media:
            BACKOFF.reset()
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
    # Explicit collaborators and original-audio attribution from the same HTML.
    def handle(user):
        name = user.get("username") if isinstance(user, dict) else None
        return name.lower() if isinstance(name, str) and config.USER_RE.fullmatch(name) else None

    owner = (out.get("owner") or "").lower()
    coauthors = m.get("coauthor_producers")
    out["coauthors"] = sorted({name for user in coauthors if isinstance(user, dict)
                              and not user.get("is_unpublished") and (name := handle(user))
                              and name != owner})[:20] if isinstance(coauthors, list) else []
    clips = m.get("clips_metadata")
    original = clips.get("original_sound_info") if isinstance(clips, dict) else None
    artist = handle(original.get("ig_artist")) if isinstance(original, dict) else None
    out["audio_creator"] = artist if artist and artist != owner else ""
    out["paid_partnership"] = m.get("is_paid_partnership") is True

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


def post_comments(code: str, first: int = 20, after: str | None = None) -> dict:
    # Coalesce panel/discovery requests without holding the metadata cache
    # lock across network I/O (reel loading must not wait for comments).
    with _COMMENT_FETCH_LOCK:
        key = (code, first, after)
        with _COMMENT_LOCK:
            cached = _COMMENT_PAGES.get(key)
            if cached and time.monotonic() - cached[0] < COMMENT_TTL:
                return deepcopy(cached[1])
        try:
            result = _fetch_comments(code, first, after)
        except InstagramError:
            with _COMMENT_LOCK:
                _COMMENT_CONTEXTS.pop(code, None)
            raise
        with _COMMENT_LOCK:
            _COMMENT_PAGES[key] = (time.monotonic(), result)
            _COMMENT_PAGES.move_to_end(key)
            while len(_COMMENT_PAGES) > 256:
                _COMMENT_PAGES.popitem(last=False)
        return deepcopy(result)


def _fetch_comments(code: str, first: int = 20, after: str | None = None) -> dict:
    """One page of a post's top-level comments, without login.

    Comments are not embedded in logged-out post pages; a separate GraphQL
    query on the post page's media id returns them. One page load seeds the
    anonymous session (cookies and tokens), then the query runs on it.
    Instagram serves about twenty comments per page no matter what `first`
    asks for; pass the returned next_cursor as `after` to keep scrolling."""
    with _COMMENT_LOCK:
        cached = _COMMENT_CONTEXTS.get(code)
    if cached and time.monotonic() - cached[0] < COMMENT_TTL:
        _, session, html = cached
    else:
        session = _new_session()
        html = _post_page(code, session=session)
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
    variables = json.dumps({"after": after, "first": first, "media_id": str(pk)},
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
    BACKOFF.reset()
    comments = []
    for edge in connection.get("edges") or []:
        node = edge.get("node") or {}
        user = node.get("user") or {}
        username = user.get("username")
        if not username:
            continue
        comments.append({
            "id": str(node.get("pk") or node.get("id") or ""),
            "username": username,
            "text": node.get("text") or "",
            "like_count": node.get("comment_like_count") or 0,
            "created_at": node.get("created_at"),
            "is_owner": username == owner,
        })
    page_info = connection.get("page_info") or {}
    has_more = bool(page_info.get("has_next_page"))
    return {"shortcode": media.get("code") or code, "owner": owner, "comments": comments,
            "next_cursor": page_info.get("end_cursor") if has_more else None,
            "has_more": has_more}


def reels_tab(username: str, first: int = 12, after: str | None = None,
              *, context: dict | None = None) -> dict:
    """One page of a profile's reels tab, without login.

    The mixed grid mixes reels with photos, but every profile also exposes a
    reels-only listing with view counts (play_count) that the grid does not
    carry. The listing is slim — no captions or timestamps; those arrive with
    the post payload when a reel is actually loaded."""
    username = username.lstrip("@").lower()
    context = context if context is not None else {}
    if context.get("session") is None:
        session, html = fetch_profile_page(username)
        match = re.search(r'"LSD",\[\],\{"token":"([\w-]+)"', html)
        context.update(session=session, lsd=match.group(1) if match else "")
    session = context["session"]
    lsd = context["lsd"]
    csrf = session.cookies.get("csrftoken", "")
    variables = json.dumps({"after": after, "first": first, "username": username},
                           separators=(",", ":"))
    body = urlencode({
        "av": "0", "__d": "www", "__user": "0", "__a": "1",
        "__comet_req": "7", "lsd": lsd,
        "jazoest": "2" + str(sum(ord(c) for c in lsd)),
        "fb_api_caller_class": "RelayModern",
        "fb_api_req_friendly_name": REELS_FRIENDLY_NAME,
        "server_timestamps": "true", "variables": variables,
        "doc_id": REELS_DOC_ID,
    })
    r = request(session, "POST", "https://www.instagram.com/api/graphql", data=body,
                headers={
                    "content-type": "application/x-www-form-urlencoded",
                    "x-fb-friendly-name": REELS_FRIENDLY_NAME,
                    "x-fb-lsd": lsd, "x-csrftoken": csrf,
                    "x-ig-app-id": WEB_APP_ID, "x-asbd-id": ASBD_ID,
                    "origin": "https://www.instagram.com",
                    "referer": f"https://www.instagram.com/{username}/reels/",
                })
    try:
        payload = r.json()
    except ValueError:
        BACKOFF.hit()
        raise RateLimited("Instagram blocked loading the reels tab; wait a few minutes") from None
    if not isinstance(payload, dict):
        raise UnexpectedResponse("Instagram sent an invalid reels listing")
    if payload.get("require_login") or payload.get("status") == "fail":
        BACKOFF.hit()
        raise RateLimited("Instagram is limiting logged-out browsing; wait a few minutes")
    if payload.get("errors"):
        raise UnexpectedResponse("Instagram would not load the reels tab; "
                                 "the query id may need refreshing")
    user = ((payload.get("data") or {}).get("xig_user_by_username") or {})
    if user.get("is_private"):
        raise ProfileUnavailable(f"@{username} is private")
    connection = (user.get("user") or user).get("polaris_clips_connection")
    if connection is None:
        raise UnexpectedResponse(f"Instagram sent no reels tab for @{username}")
    BACKOFF.reset()
    reels = []
    for edge in connection.get("edges") or []:
        node = edge.get("node") or {}
        if not node.get("code"):
            continue
        reels.append({
            "shortcode": node["code"],
            "url": f"https://www.instagram.com/reel/{node['code']}/",
            "like_count": node.get("like_count"),
            "comment_count": node.get("comment_count"),
            "play_count": node.get("play_count"),
            "image": node.get("display_uri"),
        })
    page_info = connection.get("page_info") or {}
    has_more = bool(page_info.get("has_next_page"))
    return {"username": user.get("username") or username, "reels": reels,
            "cursor": page_info.get("end_cursor") if has_more else None,
            "has_more": has_more}


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
    BACKOFF.reset()
    return user, conn, (lsd_m.group(1) if lsd_m else "")


def profile_user(html: str) -> dict | None:
    """The full account record on a profile page (name, counts, bio, links).

    The page has several xig_user_by_username objects; the one next to the
    posts only holds the account IDs, so pick the one with follower_count."""
    found = None
    for payload in sjs_payloads(html, "xig_user_by_username"):
        user = nested_find(payload, "xig_user_by_username")
        if isinstance(user, dict):
            found = user
            if "follower_count" in user:
                break
    return found


# Logged-out pages leave all_media_count empty; the meta description still
# says e.g. "681M Followers, 652 Following, 4,154 Posts - ...".
META_POST_COUNT = re.compile(r'<meta[^>]*content="[^"]*?\b(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?[KMB]?) Posts\b')
COUNT_SUFFIX = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}


def _meta_post_count(html: str) -> int | None:
    match = META_POST_COUNT.search(html)
    if not match:
        return None
    text = match.group(1).replace(",", "")
    suffix = text[-1] if text[-1] in "KMB" else ""
    return round(float(text.removesuffix(suffix)) * COUNT_SUFFIX[suffix])


def _web_link(url) -> str:
    """url when it is an ordinary http(s) link, else an empty string."""
    if not isinstance(url, str):
        return ""
    url = url.strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    return url if parts.scheme in ("http", "https") and parts.netloc else ""


def profile_details(html: str, user: dict | None = None) -> dict:
    """What instagram.com shows logged-out visitors above a profile's posts.

    Missing values are None or empty, never guessed. user is the record from
    the posts payload, used when the page has no fuller one."""
    full = profile_user(html) or user or {}
    count = full.get("all_media_count")
    timeline = full.get("edge_owner_to_timeline_media")
    if not isinstance(count, int) and isinstance(timeline, dict):
        count = timeline.get("count")
    if not isinstance(count, int):
        count = _meta_post_count(html)
    links = []
    for link in full.get("bio_links") or []:
        url = _web_link(link.get("url")) if isinstance(link, dict) else ""
        if url and url not in (known["url"] for known in links):
            links.append({"url": url, "title": str(link.get("title") or "")})
    external = _web_link(full.get("external_url"))
    if external and not links:
        links.append({"url": external, "title": ""})
    hd = full.get("hd_profile_pic_url_info") or {}
    avatar = _web_link(hd.get("url") if isinstance(hd, dict) else "") or _web_link(full.get("profile_pic_url"))
    return {
        "full_name": full.get("full_name") or "",
        "avatar": avatar,
        "biography": full.get("biography") or "",
        "followers": full.get("follower_count"),
        "following": full.get("following_count"),
        "post_count": count,
        "verified": bool(full.get("is_verified")),
        "external_url": links[0]["url"] if links else "",
        "links": links,
    }


def profile(username: str) -> tuple[dict, list[str]]:
    _, html = fetch_profile_page(username)
    if "xig_user_by_username" not in html:
        raise UnexpectedResponse(f"Instagram did not include profile data for @{username}; try again later")
    user = profile_user(html)
    codes = [c for c in dict.fromkeys(
        re.findall(r'"code":"([A-Za-z0-9_-]{10,12})"', html))]
    return user, codes


def post(url_or_code: str) -> dict:
    return _post_media(url_or_code.rstrip("/").rsplit("/", 1)[-1])


_AYML_CACHE = OrderedDict()
_AYML_LOCK = threading.Lock()
AYML_TTL = 30 * 60  # Instagram's own rail changes slowly


def related_accounts(username: str) -> list[dict]:
    """Instagram's logged-out "Accounts you might like" rail for a profile.

    The suggestions come from Instagram's similarity ranking; it mainly
    computes them for larger accounts, so smaller profiles return []."""
    username = username.lstrip("@").lower()
    with _AYML_LOCK:
        cached = _AYML_CACHE.get(username)
        if cached and time.monotonic() - cached[0] < AYML_TTL:
            return cached[1]
    session, html = fetch_profile_page(username)
    # The profile's own id, not the first "pk" on the page (posts have them too).
    user = profile_user(html) or {}
    pk = str(user.get("pk") or user.get("id") or "")
    if not pk.isdigit():
        pk_m = re.search(r'"pk":"?(\d+)"?', html)
        pk = pk_m.group(1) if pk_m else ""
    lsd_m = re.search(r'"LSD",\[\],\{"token":"([\w-]+)"', html)
    if not pk or not lsd_m:
        raise UnexpectedResponse(f"Instagram did not include tokens for @{username}; try again later")
    csrf = session.cookies.get("csrftoken", "")
    body = urlencode({
        "av": "0", "__d": "www", "__user": "0", "__a": "1",
        "__comet_req": "7", "lsd": lsd_m.group(1),
        "jazoest": "2" + str(sum(ord(c) for c in lsd_m.group(1))),
        "fb_api_caller_class": "RelayModern",
        "fb_api_req_friendly_name": AYML_FRIENDLY_NAME,
        "server_timestamps": "true",
        "variables": json.dumps({"id": pk}, separators=(",", ":")),
        "doc_id": AYML_DOC_ID,
    })
    r = request(session, "POST", "https://www.instagram.com/api/graphql", data=body,
                headers={
                    "content-type": "application/x-www-form-urlencoded",
                    "x-fb-friendly-name": AYML_FRIENDLY_NAME,
                    "x-fb-lsd": lsd_m.group(1), "x-csrftoken": csrf,
                    "x-ig-app-id": WEB_APP_ID, "x-asbd-id": ASBD_ID,
                    "origin": "https://www.instagram.com",
                    "referer": f"https://www.instagram.com/{username}/",
                })
    try:
        payload = r.json()
    except ValueError:
        BACKOFF.hit()
        raise RateLimited("Instagram blocked loading suggestions; wait a few minutes") from None
    if not isinstance(payload, dict):
        raise UnexpectedResponse("Instagram sent an invalid suggestions reply")
    if payload.get("require_login") or payload.get("status") == "fail":
        BACKOFF.hit()
        raise RateLimited("Instagram is limiting logged-out browsing; wait a few minutes")
    if payload.get("errors"):
        raise UnexpectedResponse("Instagram would not load suggestions; "
                                 "the query id may need refreshing")
    BACKOFF.reset()
    suggestions = []
    for node in (((payload.get("data") or {}).get("xig_user_by_igid_v2") or {}).get("ayml_logged_out") or []):
        name = (node.get("username") or "").lower() if isinstance(node, dict) else ""
        if not config.USER_RE.fullmatch(name) or name == username:
            continue  # it becomes a /u/<name> link in the page
        picture = node.get("profile_pic_url")
        suggestions.append({
            "username": name,
            "full_name": node.get("full_name") or "",
            "is_verified": bool(node.get("is_verified")),
            "profile_pic_url": picture if isinstance(picture, str) and picture.startswith("https://") else None,
        })
    with _AYML_LOCK:
        _AYML_CACHE[username] = (time.monotonic(), suggestions)
        _AYML_CACHE.move_to_end(username)
        while len(_AYML_CACHE) > 64:
            _AYML_CACHE.popitem(last=False)
    return suggestions


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
    STORED_KEYS = ("user_id", "username", "biography", "profile", "posts", "cursor", "has_more", "fetched_at")

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
        details = profile_details(html, user)
        if details["followers"] is None and not details["full_name"]:
            log.warning("Instagram sent no account details for @%s; the profile header will be "
                        "incomplete (the page layout may have changed)", username)
        state = {
            "session": s, "lsd": lsd,
            "csrf": s.cookies.get("csrftoken", ""),
            "user_id": user.get("id"), "username": user.get("username", username),
            "biography": details["biography"],
            "profile": details,
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
            BACKOFF.reset()
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
