from collections import Counter
import logging
import random
import threading
from . import recommendations, config, history as viewing_history
from .history import load_history
from .config import USER_RE, load_feeds, accounts_from_feed, prefer_memes, blocked_accounts
from .instagram import InstagramError, RateLimited, ProfileUnavailable
from .reels import STATE, REELS, more_reels
from .reasons import Reason
from .refill import REFILL

log = logging.getLogger(__name__)

# Unclear failures (odd replies, network trouble) are retried in later
# batches; an account is dropped for the session only after this many.
MAX_FAILURES = 3
REQUESTS_PER_BATCH = 3        # Instagram requests one batch may make, including pagination
TRIAL_REQUESTS_PER_BATCH = 1  # of those, how many may load a creator you haven't saved
UNCERTAIN_TRIAL_SHARE = 0.25  # batches that may include one unconfirmed creator
TRIAL_FIRST_SHARE = 0.25      # later picks in a batch that prefer a new creator


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


def account_snapshot():
    selected = active_feeds()
    saved = {a for members in selected.values() for a in members}
    return saved, feed_accounts() - saved, recommendations.discovery_accounts(), blocked_accounts(), bool(selected)


def available_accounts(pool: dict, tried: set, snapshot=None) -> list[str]:
    """Never use saved accounts from unselected feeds as discovery backdoors."""
    saved, other_saved, discovered, blocked, enabled = snapshot or account_snapshot()
    candidates = saved | pool['discovered'] | discovered
    return sorted(candidates - other_saved - tried - pool.get('dead', set())
                  - blocked) if enabled else []


def make_pool(seeds):
    return {"discovered": set(seeds), "seeds": set(seeds),
            "served": set(), "dead": set(), "meme_discoveries": set(), "failures": {}}


POOLS: dict[str, dict] = {}


MIX = "__mix__"

# Public meme discovery candidates, not saved memberships. Unavailable profiles
# are skipped by the normal trial loader; blocks always take precedence.
MEME_TRIAL_ACCOUNTS = ()  # Discovery comes from the user's accounts and feedback.


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


def pool_more(pool: dict, limit: int = 12, excluded=None, client="legacy-client", exclude_codes=()) -> dict:
    # Selection and reservation must be one operation across simultaneous tabs.
    with BATCH_LOCK:
        history = load_history()
        reservations = viewing_history.active_reservations()
        pool["served"] = set(history["codes"]) | {r[0] for r in reservations} | set(exclude_codes)
        excluded = set(excluded or ())
        REFILL.collect(pool, _record_failure)
        snapshot = account_snapshot()
        candidates = available_accounts(pool, excluded, snapshot)
        REFILL.schedule(pool, candidates, snapshot[0], states=STATE)
        queued = Counter(owner for _, owner, holder in reservations if holder == client)
        # Diversity must span successive refill batches, not just one response.
        cap = 2 if len(candidates) > 1 else None
        full = {owner for owner, count in queued.items() if cap and count >= cap}
        batch = _pool_more(pool, limit, excluded | full, allow_network=False, snapshot=snapshot)
        if cap:
            balanced = []
            for post in batch['posts']:
                owner = post.get('owner')
                if queued[owner] < cap:
                    balanced.append(post)
                    queued[owner] += 1
            batch['posts'] = balanced
        candidates = available_accounts(pool, excluded, snapshot)
        REFILL.schedule(pool, candidates, snapshot[0], states=STATE)
        waiting = REFILL.waiting(candidates)
        batch['refill_seconds'] = REFILL.load_seconds
        reserved_elsewhere = any(owner in candidates and holder != client for _, owner, holder in reservations)
        batch['has_more'] = bool(batch['posts']) or waiting or reserved_elsewhere or bool(full & set(candidates))
        if waiting:
            batch['retry_after_ms'] = 750
        blocked = blocked_accounts()
        posts = batch["posts"] = [p for p in batch["posts"] if p.get("owner") not in blocked]
        # Prefer a different opening account whenever this batch has one.
        for i, post in enumerate(posts):
            if post.get("owner") != history["last_owner"]:
                posts[0], posts[i] = posts[i], posts[0]
                break
        posts = batch["posts"] = viewing_history.reserve(posts, client)
        recommendations.remember_reels(posts)
        codes = [p['shortcode'] for p in posts]
        reactions = recommendations.feedback_for(codes)
        saved = recommendations.saved_codes(codes)
        for post in posts:
            post['feedback'] = reactions.get(post['shortcode'])
            post['saved'] = post['shortcode'] in saved
        recommendations.record_deliveries(posts)
        return batch



def creator_more(username, *, client, exclude_codes=(), excluded=()):
    """Explicit creator browsing uses the same bounded workers and viewing history."""
    with BATCH_LOCK:
        if username in blocked_accounts() or username in excluded:
            return {'posts': [], 'has_more': False}
        pool = POOLS.setdefault('creator:' + username, make_pool([username]))
        reservations = viewing_history.active_reservations()
        pool['served'] = set(load_history()['codes']) | set(exclude_codes) | {r[0] for r in reservations}
        REFILL.collect(pool, _record_failure)
        if username in pool['dead']:
            return {'posts': [], 'has_more': False}
        REFILL.schedule(pool, [username], {username}, states=STATE)
        state = STATE.get(username) or REELS.cached(username) or {}
        fresh = [dict(p) for p in state.get('posts', [])
                 if p.get('owner') == username and p.get('is_video') and p['shortcode'] not in pool['served']]
        reactions = recommendations.feedback_for([p['shortcode'] for p in fresh])
        fresh = [p for p in fresh if reactions.get(p['shortcode']) != 'dislike']
        if username in blocked_accounts():
            return {'posts': [], 'has_more': False}
        chosen = viewing_history.reserve(fresh[:1], client)
        recommendations.remember_reels(chosen)
        recommendations.record_deliveries(chosen)
        saved = recommendations.saved_codes([p['shortcode'] for p in chosen])
        for post in chosen:
            post.update(feedback=reactions.get(post['shortcode']), saved=post['shortcode'] in saved)
        return {'posts': chosen, 'has_more': bool(chosen) or REFILL.waiting([username]) or not state or bool(state.get('has_more')),
                'retry_after_ms': 1000}


def _pool_more(pool: dict, limit: int = 12, excluded=None, *, allow_network=True, snapshot=None) -> dict:
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
    allow_uncertain = random.random() < UNCERTAIN_TRIAL_SHARE
    uncertain_used = False
    # Bound slow Instagram requests. New-creator trials get only a small share,
    # so failed topic checks can't use up the batch before saved accounts load.
    requests_left = REQUESTS_PER_BATCH if allow_network else 0
    trial_requests_left = TRIAL_REQUESTS_PER_BATCH
    cached = None
    snapshot = snapshot or account_snapshot()
    while len(out) < min(limit, 6):
        candidates = available_accounts(pool, tried | excluded, snapshot)
        if not candidates:
            break
        if requests_left <= 0 or trial_requests_left <= 0:
            if cached is None:
                cached = REELS.cache.fresh_usernames()
            loaded = [a for a in candidates if a in STATE or a in cached]
            candidates = loaded if requests_left <= 0 else \
                loaded + [a for a in candidates if a in saved and a not in STATE and a not in cached]
            if not candidates:
                break
        # Deliver already available clips before trying cold discovery profiles.
        # A slow account must not hold playable reels behind a network request.
        ready = [a for a in candidates if a in STATE and any(
            p.get('is_video') and p['shortcode'] not in pool['served']
            for p in STATE[a]['posts'])]
        if ready:
            candidates = ready
        elif out:
            break  # Return a partial batch now; the browser refills immediately.
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
        if rested_trials and (not out or random.random() < TRIAL_FIRST_SHARE):
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
            if not allow_network and state is None:
                state = REELS.cached(username)
                if state is None:
                    continue
            # Over budget, an older in-memory copy is better than another request.
            if state is None or (requests_left > 0 and REELS.is_stale(state) and not any(
                    p.get('is_video') and p['shortcode'] not in pool['served'] for p in state['posts'])):
                state, used_network = REELS.load(username)
                if used_network:
                    requests_left, trial_requests_left = _spend_request(
                        requests_left, trial_requests_left, username in saved)
        except RateLimited as error:
            log.warning("Skipping @%s for now: %s", username, error)
            requests_left = 0  # More requests in this batch would be refused too.
            continue
        except ProfileUnavailable as error:
            log.info("Dropping @%s for this session: %s", username, error)
            pool['dead'].add(username)
            requests_left, trial_requests_left = _spend_request(requests_left, trial_requests_left, username in saved)
            continue
        except InstagramError as error:
            # A failed load still cost a request; without this a batch keeps
            # trying broken accounts one after another.
            _record_failure(pool, username, error)
            requests_left, trial_requests_left = _spend_request(requests_left, trial_requests_left, username in saved)
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
                more_reels(username)
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
            continue  # Reserved clips may become eligible again after release/expiry.
        pool.setdefault('accepted', set()).add(username)
        picked, exploratory = recommendations.pick_reel(fresh, model, view_reference=state['posts'])
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
        reason.view_boost = not exploratory and recommendations.view_count_boost(
            post, recommendations.view_baselines(state['posts'])) > 0
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


def _spend_request(requests_left, trial_requests_left, saved):
    """Count one Instagram request against the batch; trials also use their share."""
    return requests_left - 1, trial_requests_left - (0 if saved else 1)


def _record_failure(pool, username, error):
    failures = pool.setdefault('failures', {})
    failures[username] = failures.get(username, 0) + 1
    if failures[username] >= MAX_FAILURES:
        log.warning("Dropping @%s for this session after %d failures: %s", username, failures[username], error)
        pool['dead'].add(username)
    else:
        log.info("Could not load @%s (attempt %d): %s", username, failures[username], error)
