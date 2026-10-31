import logging
import random
import threading
from . import recommendations, config
from .history import load_history, save_history
from .config import USER_RE, load_feeds, accounts_from_feed, prefer_memes, blocked_accounts
from .instagram import STATE, PROFILES, more_posts, InstagramError, RateLimited, ProfileUnavailable
from .reasons import Reason

log = logging.getLogger(__name__)

# Unclear failures (odd replies, network trouble) are retried in later
# batches; an account is dropped for the session only after this many.
MAX_FAILURES = 3


def feed_accounts() -> set[str]:
    return {account for value in load_feeds().values()
            for account in accounts_from_feed(value)}


def active_feeds():
    feeds = load_feeds()
    selected = config.selected_feeds()
    return {name: accounts_from_feed(value) for name, value in feeds.items()
            if selected is None or name in selected}


def active_accounts():
    return {a for values in active_feeds().values() for a in values}


def available_accounts(pool: dict, tried: set) -> list[str]:
    """Never use saved accounts from unselected feeds as discovery backdoors."""
    saved = active_accounts()
    other_saved = feed_accounts() - saved
    candidates = saved | pool['discovered'] | recommendations.discovery_accounts()
    return sorted(candidates - other_saved - tried - pool.get('dead', set())
                  - blocked_accounts()) if active_feeds() else []


def make_pool(seeds):
    return {"discovered": set(seeds), "seeds": set(seeds),
            "served": set(), "dead": set(), "meme_discoveries": set(), "failures": {}}


POOLS: dict[str, dict] = {}


MIX = "__mix__"

# Public meme discovery candidates, not saved memberships. Unavailable profiles
# are skipped by the normal trial loader; blocks always take precedence.
MEME_TRIAL_ACCOUNTS = ("sainthoax", "epicfunnypage", "sarcasm_only", "fuckjerry", "meme")


def _pool_seeds(name: str) -> list[str]:
    feeds = load_feeds()
    if name == MIX:
        return sorted(active_accounts())
    return accounts_from_feed(feeds.get(name))


def feed_pool(name: str) -> dict:
    selection = config.selected_feeds()
    signature = (tuple((name, tuple(accounts_from_feed(value))) for name, value in load_feeds().items()),
                 tuple(selection) if selection is not None else None)
    p = POOLS.get(name)
    if p is not None and p.get("signature") == signature:
        return p
    fresh = make_pool(_pool_seeds(name))
    if p is not None:
        fresh['served'] = p['served']
    # Rebuild candidates on selection changes; don't carry unrelated old leads.
    if active_feeds().get('memes'):
        discover_into(fresh, MEME_TRIAL_ACCOUNTS, meme_source=True)
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
        codes = [p['shortcode'] for p in posts]
        reactions = recommendations.feedback_for(codes)
        saved = recommendations.saved_codes(codes)
        for post in posts:
            post['feedback'] = reactions.get(post['shortcode'])
            post['saved'] = post['shortcode'] in saved
        recommendations.record_deliveries(posts)
        save_history(history, posts)
        return batch


def _pool_more(pool: dict, limit: int = 12, excluded=None) -> dict:
    """Rank individual clips and serve one trial per creator in a varied batch."""
    excluded = set(excluded or ())
    preferred = False
    selected = active_feeds()
    saved = active_accounts()
    # Credits from saved/liked reels survive restarts and do not require the
    # user to open each reel's media before we can discover its creator.
    lead_reasons = {}
    leads = recommendations.related_leads(saved, include_liked=config.selected_feeds() is None,
                                          reasons=lead_reasons)
    discover_into(pool, leads)
    liked_creators = recommendations.discovery_accounts()
    model = recommendations.preference_model()
    recent = recommendations.recent_creators()
    cooldowns = recommendations.trial_cooldowns(saved)
    vocabulary = recommendations.topic_tag_vocabulary(selected)
    out = []
    tried = set()
    used_topics = {name: 0 for name in selected}
    # At most one uncertain trial in a quarter of batches, not a fresh lottery
    # for every tagged profile. Sponsor-only leads face an additional gate.
    allow_uncertain = random.random() < 0.25
    uncertain_used = False
    requests_left = 3  # Bound slow Instagram requests, including pagination.
    while len(out) < min(limit, 6):
        candidates = available_accounts(pool, tried | excluded)
        if not candidates:
            break
        if requests_left <= 0:
            cached = PROFILES.cache.fresh_usernames()
            candidates = [a for a in candidates if a in STATE or a in cached]
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
        rested_trials = [a for a in unfamiliar if a not in cooldowns and a not in recent]
        # Reserve the first attempt for discovery instead of leaving an entire
        # batch to chance. Failed trials may fall back to saved creators.
        if rested_trials and (not out or random.random() < 0.25):
            candidates = rested_trials
        groups = [(name, [a for a in candidates if a in members]) for name, members in selected.items()]
        groups = [(name, group) for name, group in groups if group]
        if groups and not (rested_trials and candidates == rested_trials):
            least = min(used_topics[name] for name, _ in groups)
            _, candidates = random.choice([(name, group) for name, group in groups if used_topics[name] == least])
        if candidates and all(a not in saved for a in candidates):
            username = random.choices(candidates, weights=[leads.get(a, 1.0) for a in candidates], k=1)[0]
        else:
            username = choose_account(pool, candidates, preferred)
        tried.add(username)
        try:
            state = STATE.get(username)
            # Over budget, an older in-memory copy is better than another request.
            if state is None or (requests_left > 0 and PROFILES.is_stale(state)):
                state, used_network = PROFILES.load(username)
                if used_network:
                    requests_left -= 1
        except RateLimited as error:
            log.warning("Skipping @%s for now: %s", username, error)
            requests_left = 0  # More requests in this batch would be refused too.
            continue
        except ProfileUnavailable as error:
            log.info("Dropping @%s for this session: %s", username, error)
            pool['dead'].add(username)
            continue
        except InstagramError as error:
            _record_failure(pool, username, error)
            continue
        except Exception:
            log.exception("Unexpected error loading @%s; dropping it for this session", username)
            pool['dead'].add(username)
            continue
        uncertain = False
        memberships = {name for name, accounts in selected.items() if username in accounts}
        if not memberships:
            # Curated meme starters are known candidates. Other credit/liked
            # leads must establish relevance independently of the source tag.
            memberships = {'memes'} if username in MEME_TRIAL_ACCOUNTS and selected.get('memes') else recommendations.matching_topics(state, selected, vocabulary)
            if not memberships:
                known_topics = recommendations.matching_topics(state, recommendations.TOPIC_WORDS, vocabulary)
                # A known different topic is not an uncertain match. Unknown
                # humour can get a trial, but never bypass blocks or cooldowns.
                eligible = (allow_uncertain and not uncertain_used and username in leads
                            and username not in cooldowns and username not in recent
                            and not known_topics and model[1].get(username, 0) >= 0)
                if not eligible or (leads[username] < 1 and random.random() >= 0.2):
                    continue  # Keep uncertain candidates for future batches.
                uncertain = True
        fresh = [p for p in state['posts']
                 if p.get('is_video') and p['shortcode'] not in pool['served']]
        if not fresh and state.get('has_more') and requests_left > 0:
            requests_left -= 1
            try:
                more_posts(username)
            except RateLimited as error:
                log.warning("Could not page @%s: %s", username, error)
                requests_left = 0
                continue
            except InstagramError as error:
                _record_failure(pool, username, error)
                continue
            except Exception:
                log.exception("Unexpected error paging @%s", username)
                continue
            fresh = [p for p in state['posts']
                     if p.get('is_video') and p['shortcode'] not in pool['served']]
        reactions = recommendations.feedback_for([p['shortcode'] for p in fresh])
        fresh = [p for p in fresh if reactions.get(p['shortcode']) != 'dislike']
        if not fresh:
            if not state.get('has_more'):
                pool['dead'].add(username)
            continue
        picked, exploratory = recommendations.pick_reel(fresh, model)
        # Copy: profile posts are shared with the profile view and the cache.
        post = dict(picked)
        reason = Reason(username)
        if username in saved:
            reason.saved_in = {name for name, accounts in selected.items() if username in accounts}
        else:
            reason.lead = lead_reasons.get(username)
            reason.liked_creator = username in liked_creators
            reason.starter = username in MEME_TRIAL_ACCOUNTS
            for topic in memberships:
                found = recommendations.topic_evidence(state, topic, vocabulary)
                if found:
                    reason.topics[topic] = found
        reason.uncertain = uncertain
        reason.exploratory = exploratory
        reason.features = [] if exploratory else recommendations.liked_features(post, model)
        post['reason'] = reason.as_dict()
        pool['served'].add(post['shortcode'])
        out.append(post)
        if uncertain:
            uncertain_used = True
        for topic in memberships:
            used_topics[topic] += 1
    random.shuffle(out)
    return {'posts': out, 'has_more': bool(available_accounts(pool, excluded))}


def _record_failure(pool, username, error):
    failures = pool.setdefault('failures', {})
    failures[username] = failures.get(username, 0) + 1
    if failures[username] >= MAX_FAILURES:
        log.warning("Dropping @%s for this session after %d failures: %s", username, failures[username], error)
        pool['dead'].add(username)
    else:
        log.info("Could not load @%s (attempt %d): %s", username, failures[username], error)
