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
from .topics import TOPICS, TOPIC_LEAD, TOPIC_TRIAL_SHARE, POPULAR_EVERY
from .suggestions import SUGGESTIONS, SUGGESTED_TRIAL_SHARE, SUGGESTED_REELS, DISCOVERY_AFTER

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


MAX_DISCOVERED = 300   # candidates per pool from caption, tag and comment leads
TOPIC_ROOM = 60        # extra room so topic-page creators aren't crowded out


def discover_into(pool, usernames, *, meme_source=False, limit=MAX_DISCOVERED) -> None:
    blocked = blocked_accounts()
    for u in usernames or []:
        u = (u or "").lstrip("@").lower()
        if u in blocked:
            continue
        if USER_RE.fullmatch(u) and u not in pool["discovered"] \
                and len(pool["discovered"]) < limit:
            pool["discovered"].add(u)
        if meme_source and u in pool["discovered"]:
            pool["meme_discoveries"].add(u)


def add_topic_leads(leads: dict, reasons: dict, selected: dict, snapshot) -> list[str]:
    """Add creators from the selected feeds' topic pages (see topics.py).

    These reach beyond creators connected to your feeds. Stronger evidence
    (for example an explicit credit) keeps its own reason."""
    saved, other_saved, _, blocked, _ = snapshot
    added = []
    for name, info in TOPICS.leads(selected, exclude=saved | other_saved | blocked).items():
        if leads.get(name, 0) < TOPIC_LEAD:
            leads[name] = TOPIC_LEAD
            reasons[name] = info
            added.append(name)
    return added


def add_suggested_leads(leads: dict, reasons: dict, selected: dict, snapshot) -> list[str]:
    """Add Instagram's suggested accounts for the selected feeds' creators
    (see suggestions.py), the main source of new creators."""
    saved, other_saved, _, blocked, _ = snapshot
    added = []
    for name, info in SUGGESTIONS.leads(selected, exclude=saved | other_saved | blocked).items():
        strength = SUGGESTIONS.strength(info)
        if leads.get(name, 0) < strength:
            leads[name] = strength
            reasons[name] = info
            added.append(name)
    return added


SUGGESTED_ROOM = 120   # suggested accounts' own room, beyond caption leads


def discover_leads(pool: dict, leads: dict, topic_names: list[str], suggested_names=()) -> None:
    """Fill the pool with related leads, then topic and suggested creators in
    their own room: with hundreds of related leads, the pool would otherwise be full."""
    priority = set(topic_names) | set(suggested_names)
    discover_into(pool, [name for name in leads if name not in priority])
    discover_into(pool, list(suggested_names), limit=MAX_DISCOVERED + SUGGESTED_ROOM)
    discover_into(pool, topic_names, limit=MAX_DISCOVERED + SUGGESTED_ROOM + TOPIC_ROOM)
    # Hundreds of related leads would otherwise win almost every trial;
    # refill.py and _pool_more give these creators a fixed share instead.
    pool['topic_leads'] = set(topic_names) & pool['discovered']
    pool['suggested_leads'] = set(suggested_names) & pool['discovered']


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
        selected = active_feeds()
        evidence = {}
        leads = recommendations.related_leads(snapshot[0], include_liked=config.selected_feeds() is None,
                                               topics=selected, reasons=evidence)
        topic_names = add_topic_leads(leads, evidence, selected, snapshot)
        discover_leads(pool, leads, topic_names, add_suggested_leads(leads, evidence, selected, snapshot))
        pool['topic_trials'] = {name: info for name, info in evidence.items()
                                if info.get('source') == 'popular_topic' and info.get('shortcode')}
        source_weights = recommendations.discovery_weights(evidence)
        pool['discovery_weights'] = {name: strength * source_weights.get(name, 1) for name, strength in leads.items()}
        pool['buffered'] = sum(holder == client for _, _, holder in reservations)
        pool['saved_streak'] = recommendations.saved_streak(snapshot[0],
            [owner for _, owner, holder in reservations if holder == client])
        candidates = available_accounts(pool, excluded, snapshot)
        REFILL.schedule(pool, candidates, snapshot[0], states=STATE)
        queued = Counter(owner for _, owner, holder in reservations if holder == client)
        # Diversity must span successive refill batches, not just one response.
        cap = 2 if len(candidates) > 1 else None
        full = {owner for owner, count in queued.items() if cap and count >= cap}
        # Watched clips release leases. Count them too, otherwise a fast source
        # can supply every refill indefinitely while slower sources are paging.
        sequence = viewing_history.recent_owners(client) + [owner for _, owner, holder in reservations if holder == client]
        if len(sequence) >= 2 and sequence[-1] == sequence[-2]:
            repeated = sequence[-1]
            alternatives = [name for name in candidates if name != repeated and name in snapshot[0]]
            if any(name not in STATE or STATE[name].get('has_more') or any(
                    p.get('is_video') and p['shortcode'] not in pool['served']
                    for p in STATE[name]['posts']) for name in alternatives):
                full.add(repeated)
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
        batch['has_more'] = bool(batch['posts']) or waiting or TOPICS.waiting() or reserved_elsewhere or bool(full & set(candidates))
        if waiting:
            batch['retry_after_ms'] = 750
        blocked = blocked_accounts()
        posts = batch["posts"] = [p for p in batch["posts"] if p.get("owner") not in blocked]
        # Preserve discovery positions while varying the opening saved creator.
        if posts and posts[0].get('owner') == history['last_owner']:
            first_saved = posts[0].get('owner') in snapshot[0]
            for i, post in enumerate(posts[1:], 1):
                if post.get('owner') != history['last_owner'] and (post.get('owner') in snapshot[0]) == first_saved:
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
    snapshot = snapshot or account_snapshot()
    leads = recommendations.related_leads(saved, include_liked=config.selected_feeds() is None,
                                          reasons=lead_reasons, topics=selected)
    topic_names = add_topic_leads(leads, lead_reasons, selected, snapshot)
    discover_leads(pool, leads, topic_names, add_suggested_leads(leads, lead_reasons, selected, snapshot))
    topic_trials = {name: info for name, info in lead_reasons.items()
                    if info.get('source') == 'popular_topic' and info.get('shortcode')}
    liked_creators = recommendations.discovery_accounts()
    model = recommendations.preference_model()
    recent = recommendations.recent_creators()
    cooldowns = recommendations.trial_cooldowns(saved)
    # Instagram suggested these, so one may supply a few reels, not just one trial.
    views = recommendations.recent_view_counts(20)
    cooldowns -= {name for name in pool.get('suggested_leads', ()) if views.get(name, 0) < SUGGESTED_REELS}
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
    streak = pool.get('saved_streak', 3)
    while len(out) < min(limit, 6):
        candidates = available_accounts(pool, tried | excluded, snapshot)
        if not candidates:
            break
        if requests_left <= 0 or trial_requests_left <= 0:
            if cached is None:
                cached = REELS.cache.fresh_usernames()
            loaded = [a for a in candidates if (TOPICS.trial_state(topic_trials[a]) if a in topic_trials else a in STATE or a in cached)]
            candidates = loaded if requests_left <= 0 else \
                loaded + [a for a in candidates if a in saved and a not in STATE and a not in cached]
            if not candidates:
                break
        # Deliver already available clips before trying cold discovery profiles.
        # A slow account must not hold playable reels behind a network request.
        ready = [a for a in candidates if any(
            p.get('is_video') and p['shortcode'] not in pool['served']
            for p in ((TOPICS.trial_state(topic_trials[a]) if a in topic_trials else STATE.get(a)) or {}).get('posts', []))]
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
        # Suggested accounts may return sooner (a few reels each), never back to back.
        suggested_pool = pool.get('suggested_leads', ())
        rested_trials = [a for a in unfamiliar if a not in cooldowns
                         and a not in (recent[:3] if a in suggested_pool else recent)]
        # Reserve the first attempt for discovery instead of leaving an entire
        # batch to chance. Failed trials may fall back to saved creators.
        discovery_turn = bool(rested_trials) and streak >= DISCOVERY_AFTER
        # A popular topic reel at least every POPULAR_EVERY reels, when one is loaded.
        popular_ready = [a for a in candidates if a in topic_trials]
        if popular_ready and pool.get('since_popular', 0) >= POPULAR_EVERY - 1:
            candidates = popular_ready
            discovery_turn = True
        elif discovery_turn:
            candidates = rested_trials
            # Instagram's suggested accounts first, then popular topics, then caption leads.
            from_suggested = [a for a in candidates if a in pool.get('suggested_leads', ())]
            from_topics = [a for a in candidates if a in pool.get('topic_leads', ())]
            if from_suggested and random.random() < SUGGESTED_TRIAL_SHARE:
                candidates = from_suggested
            elif from_topics and random.random() < TOPIC_TRIAL_SHARE:
                candidates = from_topics
        elif any(a in saved for a in candidates):
            candidates = [a for a in candidates if a in saved]
        groups = [(name, [a for a in candidates if a in members]) for name, members in selected.items()]
        groups = [(name, group) for name, group in groups if group]
        if groups and not discovery_turn:
            least = min(used_topics[name] for name, _ in groups)
            _, candidates = random.choice([(name, group) for name, group in groups if used_topics[name] == least])
        if candidates and all(a not in saved for a in candidates):
            username = random.choices(candidates, weights=[leads.get(a, 1.0) for a in candidates], k=1)[0]
        else:
            username = choose_account(pool, candidates, preferred)
        tried.add(username)
        try:
            topic_trial = topic_trials.get(username)
            if topic_trial:
                state = TOPICS.trial_state(topic_trial)
                if state is None and allow_network and requests_left > 0 and trial_requests_left > 0:
                    state = TOPICS.trial_state(topic_trial, network=True)
                    requests_left, trial_requests_left = _spend_request(requests_left, trial_requests_left, False)
                if state is None:
                    continue
            else:
                state = STATE.get(username)
            if not allow_network and state is None:
                state = REELS.cached(username)
                if state is None:
                    continue
            # Over budget, an older in-memory copy is better than another request.
            if not topic_trial and (state is None or (requests_left > 0 and REELS.is_stale(state) and not any(
                    p.get('is_video') and p['shortcode'] not in pool['served'] for p in state['posts']))):
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
        topic_lead = lead_reasons.get(username) or {}
        if not memberships and topic_trial and topic_lead.get('feed') in selected:
            # Evidence applies only to this exact reel, never the whole creator.
            memberships = {topic_lead['feed']}
        if not memberships and topic_lead.get('source') == 'suggested' and topic_lead.get('feed') in selected:
            # Instagram suggested this account for a creator in that feed.
            memberships = {topic_lead['feed']}
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
        if not topic_trial:
            pool.setdefault('accepted', set()).add(username)
        picked, exploratory = recommendations.pick_feed_reel(fresh, model, view_reference=state['posts'])
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
        if username not in saved:
            post['discovery_source'] = (lead_reasons.get(username) or {}).get('source', 'liked_creator' if username in liked_creators else 'other')
        pool['served'].add(post['shortcode'])
        out.append(post)
        streak = streak + 1 if username in saved else 0
        pool['since_popular'] = 0 if username in topic_trials else pool.get('since_popular', 0) + 1
        if uncertain:
            uncertain_used = True
        for topic in memberships:
            used_topics[topic] += 1
    return {'posts': out, 'has_more': bool(available_accounts(pool, excluded, snapshot))}


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
