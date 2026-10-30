import random
import threading
from . import recommendations
from .history import load_history, save_history
from .config import USER_RE, load_feeds, accounts_from_feed, prefer_memes, blocked_accounts
from .instagram import STATE, init_state, more_posts


def feed_accounts() -> set[str]:
    return {account for value in load_feeds().values()
            for account in accounts_from_feed(value)}


def available_accounts(pool: dict, tried: set) -> list[str]:
    """Eligible saved and discovered accounts, excluding blocks and failed trials."""
    return sorted((feed_accounts() | pool["discovered"] | recommendations.discovery_accounts())
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
    signature = tuple((name, tuple(accounts_from_feed(value))) for name, value in load_feeds().items())
    p = POOLS.get(name)
    if p is not None and p.get("signature") == signature:
        return p
    fresh = make_pool(_pool_seeds(name))
    if p is not None:  # saved feeds changed: keep memory, honor removals
        fresh["served"] = p["served"]
        fresh["meme_discoveries"] = p["meme_discoveries"] - p["seeds"]
        # carry over only TRUE discoveries; old seeds that vanished from
        # saved feeds were removed by the user and must stay gone
        for u in p["discovered"] - p["seeds"]:
            fresh["discovered"].add(u)
    fresh["signature"] = signature
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


def pool_more(pool: dict, limit: int = 12, excluded=None) -> dict:
    # Selection and reservation must be one operation across simultaneous tabs.
    with BATCH_LOCK:
        history = load_history()
        pool["served"].update(history["codes"])
        batch = _pool_more(pool, limit, set(excluded or ()))
        blocked = blocked_accounts()
        posts = batch["posts"] = [p for p in batch["posts"] if p.get("owner") not in blocked]
        # Prefer a different opening account whenever this batch has one.
        for i, post in enumerate(posts):
            if post.get("owner") != history["last_owner"]:
                posts[0], posts[i] = posts[i], posts[0]
                break
        recommendations.remember_reels(posts)
        reactions = recommendations.feedback_for([p['shortcode'] for p in posts])
        for post in posts:
            post['feedback'] = reactions.get(post['shortcode'])
        recommendations.record_deliveries(posts)
        save_history(history, posts)
        return batch


def _pool_more(pool: dict, limit: int = 12, excluded=None) -> dict:
    """Rank individual clips and serve one trial per creator in a varied batch."""
    excluded = set(excluded or ())
    preferred = prefer_memes()
    saved = feed_accounts()
    model = recommendations.preference_model()
    recent = recommendations.recent_creators()
    cooldowns = recommendations.trial_cooldowns(saved)
    out = []
    tried = set()
    requests_left = 3  # Bound slow Instagram requests, including pagination.
    while len(out) < min(limit, 6):
        candidates = available_accounts(pool, tried | excluded)
        if not candidates:
            break
        if requests_left <= 0:
            candidates = [a for a in candidates if a in STATE]
            if not candidates:
                break
        # Avoid repeating recent creators when alternatives are available.
        diverse = [a for a in candidates if a not in recent and a not in cooldowns]
        if diverse:
            candidates = diverse
        else:
            rested = [a for a in candidates if a not in cooldowns]
            if rested:
                candidates = rested
        unfamiliar = [a for a in candidates if a not in saved]
        if unfamiliar and random.random() < 0.25:
            candidates = unfamiliar
        username = choose_account(pool, candidates, preferred)
        tried.add(username)
        try:
            if username in STATE:
                state = STATE[username]
            else:
                requests_left -= 1
                state = init_state(username)
        except (Exception, SystemExit):
            pool['dead'].add(username)
            continue
        fresh = [p for p in state['posts']
                 if p.get('is_video') and p['shortcode'] not in pool['served']]
        if not fresh and state.get('has_more') and requests_left > 0:
            requests_left -= 1
            try:
                more_posts(username)
            except (Exception, SystemExit):
                continue
            fresh = [p for p in state['posts']
                     if p.get('is_video') and p['shortcode'] not in pool['served']]
        reactions = recommendations.feedback_for([p['shortcode'] for p in fresh])
        fresh = [p for p in fresh if reactions.get(p['shortcode']) != 'dislike']
        if not fresh:
            if not state.get('has_more'):
                pool['dead'].add(username)
            continue
        post = recommendations.choose_reel(fresh, model)
        pool['served'].add(post['shortcode'])
        out.append(post)
    random.shuffle(out)
    return {'posts': out, 'has_more': bool(available_accounts(pool, excluded))}
