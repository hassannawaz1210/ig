import random
import threading
from .history import load_history, save_history
from .config import FEEDS_JSON, USER_RE, load_feeds, accounts_from_feed, prefer_memes, blocked_accounts
from .instagram import STATE, init_state, more_posts


def feed_accounts() -> set[str]:
    return {account for value in load_feeds().values()
            for account in accounts_from_feed(value)}


def available_accounts(pool: dict, tried: set) -> list[str]:
    """Each eligible account gets the same chance, including discoveries."""
    return sorted((feed_accounts() | pool["discovered"])
                  - tried - pool.get("dead", set()) - blocked_accounts())


def make_pool(seeds):
    return {"discovered": set(seeds), "seeds": set(seeds),
            "served": set(), "dead": set(), "meme_discoveries": set()}


POOLS: dict[str, dict] = {}


MIX = "__mix__"


def _pool_seeds(name: str) -> list[str]:
    feeds = load_feeds()
    if name == MIX:
        return sorted({a for val in feeds.values() for a in accounts_from_feed(val)})
    return accounts_from_feed(feeds.get(name))


def feed_pool(name: str) -> dict:
    mtime = FEEDS_JSON.stat().st_mtime if FEEDS_JSON.exists() else 0
    p = POOLS.get(name)
    if p is not None and p.get("mtime") == mtime:
        return p
    fresh = make_pool(_pool_seeds(name))
    if p is not None:  # feeds.json changed: keep memory, honor removals
        fresh["served"] = p["served"]
        fresh["meme_discoveries"] = p["meme_discoveries"] - p["seeds"]
        # carry over only TRUE discoveries; old seeds that vanished from
        # feeds.json were removed by the user and must stay gone
        for u in p["discovered"] - p["seeds"]:
            fresh["discovered"].add(u)
    fresh["mtime"] = mtime
    POOLS[name] = fresh
    return fresh


def discover_into(pool, usernames, *, meme_source=False) -> None:
    blocked = blocked_accounts()
    for u in usernames or []:
        u = (u or "").lstrip("@").lower()
        if u in blocked:
            continue
        if USER_RE.fullmatch(u) and u not in pool["discovered"] \
                and len(pool["discovered"]) < 300:
            pool["discovered"].add(u)
        if meme_source and u in pool["discovered"]:
            pool["meme_discoveries"].add(u)


def discover(usernames) -> None:
    """Discover into the general reels pool (used by profile-grid zooms)."""
    discover_into(feed_pool("reels"), usernames)


def choose_account(pool: dict, candidates: list[str], preferred: bool) -> str:
    if not preferred:
        return random.choice(candidates)
    saved = set(accounts_from_feed(load_feeds().get("memes")))
    discovered = pool["meme_discoveries"] - saved
    groups = [([a for a in candidates if a in saved], 70),
              ([a for a in candidates if a in discovered], 20),
              ([a for a in candidates if a not in saved | discovered], 10)]
    groups = [(accounts, chance) for accounts, chance in groups if accounts]
    # Redistribute missing groups' share among the available groups.
    selected = random.choices([g[0] for g in groups],
                              weights=[g[1] for g in groups], k=1)[0]
    return random.choice(selected)


BATCH_LOCK = threading.Lock()


def pool_more(pool: dict, limit: int = 12, per_profile: int = 4, excluded=None) -> dict:
    # Selection and reservation must be one operation across simultaneous tabs.
    with BATCH_LOCK:
        history = load_history()
        pool["served"].update(history["codes"])
        batch = _pool_more(pool, limit, per_profile, set(excluded or ()))
        blocked = blocked_accounts()
        posts = batch["posts"] = [p for p in batch["posts"] if p.get("owner") not in blocked]
        # Prefer a different opening account whenever this batch has one.
        for i, post in enumerate(posts):
            if post.get("owner") != history["last_owner"]:
                posts[0], posts[i] = posts[i], posts[0]
                break
        save_history(history, posts)
        return batch


def _pool_more(pool: dict, limit: int = 12, per_profile: int = 4, excluded=None) -> dict:
    """Pick random accounts and unserved videos, including discovered accounts."""
    excluded = set(excluded or ())
    preferred = prefer_memes()
    out: list[dict] = []
    profiles_used = 0
    tried: set[str] = set()
    while len(out) < limit and profiles_used < 3:
        candidates = available_accounts(pool, tried | excluded)
        if not candidates:
            break
        username = choose_account(pool, candidates, preferred)
        tried.add(username)
        try:
            st = STATE.get(username) or init_state(username)
        except (Exception, SystemExit):  # dead/gated account
            pool["dead"].add(username)
            continue
        fresh = [p for p in st["posts"]
                 if p.get("is_video") and p["shortcode"] not in pool["served"]]
        pages = 0
        while not fresh and st.get("has_more") and pages < 3:
            pages += 1
            page = more_posts(username)  # one more page (~50 posts)
            fresh = [p for p in st["posts"]
                     if p.get("is_video") and p["shortcode"] not in pool["served"]]
            if page.get("error") or not page.get("posts"):
                break
        if not fresh:
            if not st.get("has_more"):
                pool["dead"].add(username)
            continue  # no unserved videos here; try another account
        take = random.sample(fresh, min(per_profile, len(fresh), limit - len(out)))
        for p in take:
            pool["served"].add(p["shortcode"])
        random.shuffle(take)
        out += take
        profiles_used += 1
    random.shuffle(out)
    return {"posts": out, "has_more": bool(available_accounts(pool, excluded))}
