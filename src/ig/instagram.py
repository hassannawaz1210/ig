import json
import random
import re
import threading
import time
from urllib.parse import urlencode
from curl_cffi import requests as cr


POSTS_DOC_ID = "28904005159232689"


POSTS_FRIENDLY_NAME = "PolarisLoggedOutDesktopWWWProfilePostsTabContentQuery_connection"


UA_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


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


def media_info(code: str) -> dict:
    """Full data for one post incl. direct MP4 URL (reels), via the
    post page's xig_polaris_media payload, including audio and tagged creators."""
    s = cr.Session(impersonate="chrome", headers=UA_HEADERS)
    html = s.get(f"https://www.instagram.com/p/{code}/", timeout=30).text
    m = None
    for payload in sjs_payloads(html, "xig_polaris_media"):
        media = nested_find(payload, "xig_polaris_media")
        if media:
            m = media.get("if_not_gated_logged_out") or media
            break
    if m is None:
        raise SystemExit(f"no post payload for {code}")
    def best(vlist):
        return (max(vlist, key=lambda x: x.get("width") or 0).get("url")
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


def fetch_profile_page(username: str):
    """GET the profile page; return (session, html)."""
    s = cr.Session(impersonate="chrome", headers=UA_HEADERS)
    r = s.get(f"https://www.instagram.com/{username}/", timeout=30)
    r.raise_for_status()
    return s, r.text


def parse_profile(html: str):
    """Extract (user, first_connection, lsd) from profile page HTML."""
    lsd_m = re.search(r'"LSD",\[\],\{"token":"([\w-]+)"', html)
    user = conn = None
    for payload in sjs_payloads(html, "polaris_ordered_timeline_connection"):
        user = user or nested_find(payload, "xig_user_by_username")
        conn = nested_find(payload, "polaris_ordered_timeline_connection")
        if conn and conn.get("edges"):
            break
    if not conn:
        raise SystemExit(f"no timeline payload (retry or slow down)")
    return user, conn, (lsd_m.group(1) if lsd_m else "")


def profile(username: str) -> tuple[dict, list[str]]:
    _, html = fetch_profile_page(username)
    if "xig_user_by_username" not in html:
        raise SystemExit(f"no data payload for @{username} (retry or slow down)")
    user = None
    for payload in sjs_payloads(html, "xig_user_by_username"):
        user = nested_find(payload, "xig_user_by_username")
        if user and "follower_count" in user:
            break
    codes = [c for c in dict.fromkeys(
        re.findall(r'"code":"([A-Za-z0-9_-]{10,12})"', html))]
    return user, codes


def post(url_or_code: str) -> dict:
    code = url_or_code.rstrip("/").rsplit("/", 1)[-1]
    s = cr.Session(impersonate="chrome", headers=UA_HEADERS)
    html = s.get(f"https://www.instagram.com/p/{code}/", timeout=30).text
    for payload in sjs_payloads(html, "xig_polaris_media"):
        media = nested_find(payload, "xig_polaris_media")
        if media:
            return media.get("if_not_gated_logged_out") or media
    raise SystemExit(f"no post payload for {code}")


STATE: dict[str, dict] = {}


def init_state(username: str) -> dict:
    """Fresh session; seed first 12 posts. No cache — always starts live."""
    s, html = fetch_profile_page(username)
    user, conn, lsd = parse_profile(html)
    state = {
        "session": s, "lsd": lsd,
        "csrf": s.cookies.get("csrftoken", ""),
        "user_id": user["id"], "username": user.get("username", username),
        "posts": [slim(e["node"]) for e in conn.get("edges") or []],
        "cursor": (conn.get("page_info") or {}).get("end_cursor"),
        "has_more": (conn.get("page_info") or {}).get("has_next_page", False),
        "lock": threading.Lock(),
    }
    STATE[username] = state
    return state


def more_posts(username: str) -> dict:
    """One pagination step (~50 posts). Called by the Load-more button."""
    state = STATE.get(username) or init_state(username)
    with state["lock"]:
        if not state.get("has_more") or not state.get("cursor"):
            return {"posts": [], "has_more": False}
        time.sleep(random.uniform(1.0, 2.2))  # stay under the hourly cap
        s: cr.Session = state["session"]
        lsd = state["lsd"]
        variables = json.dumps({
            "after": state["cursor"], "first": 50, "id": state["user_id"],
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
        r = s.post("https://www.instagram.com/api/graphql", data=body,
                   timeout=30, headers={
                       "content-type": "application/x-www-form-urlencoded",
                       "x-fb-friendly-name": POSTS_FRIENDLY_NAME,
                       "x-fb-lsd": lsd, "x-csrftoken": state["csrf"],
                       "x-ig-app-id": "936619743392459", "x-asbd-id": "359341",
                       "origin": "https://www.instagram.com",
                       "referer": f"https://www.instagram.com/{username}/",
                   })
        try:
            c = ((r.json().get("data") or {}).get("node") or {}) \
                .get("polaris_ordered_timeline_connection") or {}
        except ValueError:
            return {"error": "blocked or rate limited — wait a few minutes",
                    "has_more": True}
        edges = c.get("edges") or []
        if not edges:
            return {"error": "no posts (stale doc_id or rate limit)",
                    "has_more": True}
        fresh = [slim(e["node"]) for e in edges]
        state["posts"] += fresh
        pi = c.get("page_info") or {}
        state["cursor"] = pi.get("end_cursor")
        state["has_more"] = bool(pi.get("has_next_page"))
        return {"posts": fresh, "has_more": state["has_more"],
                "total": len(state["posts"])}
