"""Local reel preferences, topic matching, and trial-account discovery."""
import json
import logging
import math
import random
import re
import sqlite3
import time
from collections import Counter
from statistics import median
from contextlib import contextmanager
from . import config
from .migrations import migrate

log = logging.getLogger(__name__)

STOP_WORDS = set('this that with from your have just when what like follow share viral reels reel instagram explore fyp foryou trending funny video videos meme memes content original audio sound http https www com the and for you are not but'.split())


TAG_RE = re.compile(r'#([\w]{2,40})')

# Reach-bait hashtags that say nothing about a reel's topic.
GENERIC_TAGS = set('''explore explorepage explorer explorepage✨ instagood instagram instadaily insta instareels
reelsinstagram reelitfeelit reelkarofeelkaro viralreels viralvideos viralvideo viralpost foryoupage
fypシ fyp foryou trendingreels trendingnow trend love photooftheday followme follow4follow like4like
likeforlikes tiktok youtube shorts reels reel viral trending funny memes meme'''.split())

MAX_REELS = 10000
PRUNE_SLACK = 500
DATABASE_TIMEOUT = 10  # seconds to wait for another process's write

# Comment-derived leads: the reel's own creator replying with a @handle is a
# deliberate mention, while commenters in general are the weakest evidence
# there is — fans, not creators — so only a few are kept and they weigh less
# than a sponsored mention.
COMMENTER_LEAD = 0.15
REPLY_MENTION_LEAD = 1.0
MAX_COMMENTERS_KEPT = 8
MAX_REPLY_MENTIONS_KEPT = 4

# A tag is "generic" once this many creators use it, or this share of all
# creators in the index, whichever is larger.
GENERIC_TAG_MIN_CREATORS = 8
GENERIC_TAG_CREATOR_SHARE = 0.15
MAX_ANCHOR_TAGS = 60       # your most-used specific tags considered for leads
MAX_TAG_LEADS = 60         # creators recruited through shared tags per batch
LIKED_TAG_WEIGHT = 2       # a liked reel's tags count this many times
TAG_LEAD_BASE = 0.5        # strength of a lead sharing one tag
TAG_LEAD_STEP = 0.25       # added per extra shared tag
TAG_LEAD_MAX = 1.5         # below an explicit credit (2.0)
TAGS_SHOWN_IN_REASON = 3
EXPLORATORY_SHARE = 0.2    # share of picks made at random on purpose
LIKED_FEATURE_MIN_WEIGHT = 0.25


def caption_tags(text):
    """Distinct lowercase hashtags of a caption, bounded."""
    return list(dict.fromkeys(t for t in TAG_RE.findall((text or '').lower())))[:30]


def _initial_schema(connection):
    connection.execute('CREATE TABLE IF NOT EXISTS reels (code TEXT PRIMARY KEY, data TEXT NOT NULL, updated REAL NOT NULL)')
    connection.execute('CREATE TABLE IF NOT EXISTS feedback (code TEXT PRIMARY KEY, action TEXT NOT NULL, updated REAL NOT NULL)')
    connection.execute('CREATE TABLE IF NOT EXISTS deliveries (id INTEGER PRIMARY KEY, code TEXT NOT NULL, owner TEXT NOT NULL, sent REAL NOT NULL)')


def _tag_index_schema(connection):
    """v2: local hashtag index for discovery and topic matching.

    Instagram serves no logged-out hashtag pages, so this index is built from
    captions already fetched through public profiles and reels. Existing
    stored reels are backfilled in this same transaction."""
    connection.execute('CREATE TABLE IF NOT EXISTS tag_index '
                       '(tag TEXT NOT NULL, code TEXT NOT NULL, owner TEXT NOT NULL, updated REAL NOT NULL, '
                       'PRIMARY KEY (tag, code))')
    connection.execute('CREATE INDEX IF NOT EXISTS tag_index_owner ON tag_index (owner)')
    connection.execute('CREATE INDEX IF NOT EXISTS tag_index_code ON tag_index (code)')
    rows = []
    for code, raw, updated in connection.execute('SELECT code, data, updated FROM reels'):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for tag in caption_tags(data.get('caption')):
            rows.append((tag, code, data.get('owner') or '', updated))
    connection.executemany('INSERT OR REPLACE INTO tag_index VALUES (?, ?, ?, ?)', rows)


def _saved_reels_schema(connection):
    """v3: reels the user saved with S, kept with their metadata.

    A copy of the display metadata is kept here, so saved reels survive the
    pruning of the reels table and can be listed without contacting Instagram."""
    connection.execute('CREATE TABLE IF NOT EXISTS saved_reels '
                       '(code TEXT PRIMARY KEY, owner TEXT NOT NULL, caption TEXT, url TEXT NOT NULL, '
                       'taken_at INTEGER, like_count INTEGER, comment_count INTEGER, image TEXT, '
                       'saved_at REAL NOT NULL)')
    connection.execute('CREATE INDEX IF NOT EXISTS saved_reels_saved_at ON saved_reels (saved_at)')


def _viewed_reels_schema(connection):
    connection.execute('CREATE TABLE IF NOT EXISTS viewed_reels (event TEXT PRIMARY KEY, code TEXT NOT NULL, owner TEXT NOT NULL, viewed REAL NOT NULL)')


def _watch_log_schema(connection):
    # Permanent, compact record of what you watched, for the stats page. Unlike
    # viewed_reels (last 200) and the viewing history (last 5000), it is never
    # trimmed; one small row per reel watched.
    connection.execute('CREATE TABLE IF NOT EXISTS watch_log (token TEXT PRIMARY KEY, code TEXT NOT NULL, '
                       'owner TEXT NOT NULL, viewed REAL NOT NULL, seconds REAL NOT NULL, '
                       'source TEXT NOT NULL, feed TEXT, tags TEXT NOT NULL)')
    connection.execute('CREATE INDEX IF NOT EXISTS watch_log_viewed ON watch_log(viewed)')


def _watch_log_code_index(connection):
    # Finds a reel's views quickly, so the stats page can match likes and
    # skips to the view they belong to.
    connection.execute('CREATE INDEX IF NOT EXISTS watch_log_code ON watch_log(code, viewed)')


MIGRATIONS = (_initial_schema, _tag_index_schema, _saved_reels_schema, _viewed_reels_schema, _watch_log_schema,
              _watch_log_code_index)


@contextmanager
def database():
    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(config.CONFIG_DIR / 'recommendations.sqlite3', timeout=DATABASE_TIMEOUT)
    try:
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            migrate(connection, MIGRATIONS)
            yield connection
    finally:
        connection.close()


def remember_reels(posts):
    """Keep only recommendation metadata, never video URLs or media files."""
    now = time.time()
    with database() as connection:
        for post in posts:
            code = post.get('shortcode')
            if not code:
                continue
            old = connection.execute('SELECT data FROM reels WHERE code = ?', (code,)).fetchone()
            data = json.loads(old[0]) if old else {'shortcode': code}
            before = (data.get('caption'), data.get('owner'))
            for key in ('owner', 'caption', 'tagged_users', 'audio_id', 'song',
                        'commenters', 'reply_mentions', 'coauthors', 'audio_creator', 'paid_partnership', 'discovery_source'):
                if post.get(key) is not None:
                    data[key] = post[key]
            connection.execute('INSERT OR REPLACE INTO reels VALUES (?, ?, ?)',
                               (code, json.dumps(data), now))
            if old and before != (data.get('caption'), data.get('owner')):
                # An edited caption must not keep its removed hashtags.
                connection.execute('DELETE FROM tag_index WHERE code = ?', (code,))
            tags = caption_tags(data.get('caption'))
            if tags:
                owner = data.get('owner') or ''
                connection.executemany('INSERT OR REPLACE INTO tag_index VALUES (?, ?, ?, ?)',
                                       [(tag, code, owner, now) for tag in tags])
        _prune_reels(connection)


def _prune_reels(connection):
    """Bound unreviewed reels and their hashtag rows; keep reels with feedback
    and saved reels.

    Runs only after the table has grown past a margin, instead of scanning
    both tables on every call."""
    if connection.execute('SELECT COUNT(*) FROM reels').fetchone()[0] <= MAX_REELS + PRUNE_SLACK:
        return
    old = [(row[0],) for row in connection.execute(
        'SELECT code FROM reels WHERE code NOT IN (SELECT code FROM feedback) '
        'AND code NOT IN (SELECT code FROM saved_reels) '
        'AND code NOT IN (SELECT code FROM reels ORDER BY updated DESC LIMIT ?)', (MAX_REELS,))]
    connection.executemany('DELETE FROM reels WHERE code = ?', old)
    connection.executemany('DELETE FROM tag_index WHERE code = ?', old)


def set_feedback(code, action):
    if action not in {'like', 'dislike', 'skip', 'clear'}:
        raise ValueError('Unknown feedback action')
    with database() as connection:
        if not connection.execute('SELECT 1 FROM reels WHERE code = ?', (code,)).fetchone():
            raise KeyError('Reel has not been loaded')
        previous = connection.execute('SELECT action FROM feedback WHERE code = ?', (code,)).fetchone()
        if action == 'skip' and previous and previous[0] in {'like', 'dislike'}:
            return previous[0]
        if action == 'clear':
            connection.execute('DELETE FROM feedback WHERE code = ?', (code,))
            return None
        connection.execute('INSERT OR REPLACE INTO feedback VALUES (?, ?, ?)', (code, action, time.time()))
        return action


def stored_reel(code):
    """The stored recommendation metadata of a loaded reel, or None."""
    with database() as connection:
        row = connection.execute('SELECT data FROM reels WHERE code = ?', (code,)).fetchone()
    return json.loads(row[0]) if row else None


def _whole_number(value):
    """An integer field from Instagram data, or None for anything else."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _text(value):
    return value if isinstance(value, str) else None


def set_reel_saved(code, saved, details=None):
    """Save or unsave one reel locally and return whether it is saved.

    Saving is separate from likes: it changes no feedback, feeds or ranking.
    details holds display metadata (caption, image, counts...) known for the
    reel; anything missing falls back to the stored reel data. Raises KeyError
    when saving a reel that has never been loaded."""
    if type(saved) is not bool:
        raise ValueError('saved must be true or false')
    with database() as connection:
        if not saved:
            connection.execute('DELETE FROM saved_reels WHERE code = ?', (code,))
            return False
        row = connection.execute('SELECT data FROM reels WHERE code = ?', (code,)).fetchone()
        if not row:
            raise KeyError('Reel has not been loaded')
        data = {**json.loads(row[0]), **{k: v for k, v in (details or {}).items() if v is not None}}
        image = _text(data.get('image'))
        url = _text(data.get('url'))
        values = (code, _text(data.get('owner')) or '', _text(data.get('caption')),
                  url if url and url.startswith('https://') else f'https://www.instagram.com/p/{code}/',
                  _whole_number(data.get('taken_at')), _whole_number(data.get('like_count')),
                  _whole_number(data.get('comment_count')),
                  image if image and image.startswith('https://') else None, time.time())
        # Saving again refreshes the metadata but keeps the original saved time.
        connection.execute('INSERT INTO saved_reels VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) '
                           'ON CONFLICT (code) DO UPDATE SET owner = excluded.owner, caption = excluded.caption, '
                           'url = excluded.url, taken_at = excluded.taken_at, like_count = excluded.like_count, '
                           'comment_count = excluded.comment_count, image = excluded.image', values)
        return True


def saved_codes(codes):
    """Which of these shortcodes are saved reels."""
    if not codes:
        return set()
    with database() as connection:
        return {row[0] for row in connection.execute(
            'SELECT code FROM saved_reels WHERE code IN (' + ','.join('?' for _ in codes) + ')', list(codes))}


def saved_reels():
    """All saved reels, newest saved first, shaped like profile posts."""
    with database() as connection:
        rows = connection.execute(
            'SELECT code, owner, caption, url, taken_at, like_count, comment_count, image, saved_at '
            'FROM saved_reels ORDER BY saved_at DESC').fetchall()
    return [{'shortcode': code, 'owner': owner, 'caption': caption, 'url': url, 'taken_at': taken_at,
             'like_count': like_count, 'comment_count': comment_count, 'image': image,
             'saved_at': saved_at, 'is_video': True, 'saved': True}
            for code, owner, caption, url, taken_at, like_count, comment_count, image, saved_at in rows]


def feedback_for(codes):
    if not codes:
        return {}
    with database() as connection:
        rows = connection.execute('SELECT code, action FROM feedback WHERE code IN (' +
                                  ','.join('?' for _ in codes) + ')', list(codes)).fetchall()
        return dict(rows)


def liked_reels():
    with database() as connection:
        return [json.loads(row[0]) for row in connection.execute(
            'SELECT r.data FROM reels r JOIN feedback f ON r.code=f.code '
            "WHERE f.action='like' ORDER BY f.updated DESC LIMIT 300")]


def discovery_accounts():
    """A like approves the reel's creator, never every account tagged in it."""
    return {p['owner'].lower() for p in liked_reels()
            if isinstance(p.get('owner'), str) and config.USER_RE.fullmatch(p['owner'])}


def creator_credits(post):
    """Explicit attribution is stronger evidence than a plain mention."""
    return {name.rstrip('.').lower() for name in re.findall(
        r'(?i)(?:credits?|video by|created by|original by|creator)\s*[:：-]?\s*@([A-Za-z0-9._]{1,30})', post.get('caption') or '')
        if config.USER_RE.fullmatch(name)}


PROMOTIONAL_RE = re.compile(r'(?i)(#ad\b|#sponsored\b|paid partnership|sponsored by|partnering with|promo code|discount code)')


def remember_commenters(code, result):
    """Keep the discovery signal of one post's comments.

    Commenters are the weakest leads; @handles in the reel creator's own
    replies are deliberate mentions and count like caption mentions."""
    comments = (result or {}).get('comments') or []
    owner = (result or {}).get('owner')
    commenters = []
    reply_mentions = []
    for comment in sorted(comments, key=lambda c: -(c.get('like_count') or 0)):
        name = (comment.get('username') or '').lower()
        if not name or not config.USER_RE.fullmatch(name):
            continue
        if comment.get('is_owner') or name == owner:
            # The creator's own replies: their @handles are deliberate mentions.
            reply_mentions += re.findall(r'@([A-Za-z0-9._]{1,30})',
                                         comment.get('text') or '')
        else:
            commenters.append(name)
    commenters = list(dict.fromkeys(commenters))[:MAX_COMMENTERS_KEPT]
    reply_mentions = [name.rstrip('.').lower() for name in reply_mentions
                      if config.USER_RE.fullmatch(name.rstrip('.').lower())]
    remember_reels([{'shortcode': code, 'commenters': commenters,
                     'reply_mentions': list(dict.fromkeys(reply_mentions))[:MAX_REPLY_MENTIONS_KEPT]}])


def candidate_lead_kinds(post):
    """Leads from one reel as {name: (strength, kind)}.

    Explicit coauthors outrank plain credits; audio attribution is weaker.
    Sponsored evidence is discounted, and stronger evidence wins per handle."""
    caption = post.get('caption') or ''
    handles = set(post.get('tagged_users') or [])
    handles.update(re.findall(r'@([A-Za-z0-9._]{1,30})', caption))
    promotional = bool(PROMOTIONAL_RE.search(caption)) or post.get('paid_partnership') is True
    leads = {name.rstrip('.').lower(): (0.2, 'sponsored_mention') if promotional else (1.0, 'mention')
             for name in handles if isinstance(name, str) and config.USER_RE.fullmatch(name)}
    for name in creator_credits(post):
        leads[name] = (0.5, 'sponsored_credit') if promotional else (2.0, 'credit')
    owner = (post.get('owner') or '').lower()
    coauthors = post.get('coauthors')
    for name in coauthors if isinstance(coauthors, list) else []:
        if not isinstance(name, str) or not config.USER_RE.fullmatch(name) or name.lower() == owner:
            continue
        name = name.lower()
        strength, kind = (0.5, 'sponsored_coauthor') if promotional else (2.5, 'coauthor')
        if leads.get(name, (0,))[0] < strength:
            leads[name] = (strength, kind)
    name = post.get('audio_creator')
    if isinstance(name, str) and config.USER_RE.fullmatch(name) and name.lower() != owner:
        name = name.lower()
        strength, kind = (0.1, 'sponsored_audio_creator') if promotional else (0.4, 'audio_creator')
        if leads.get(name, (0,))[0] < strength:
            leads[name] = (strength, kind)
    for name in post.get('reply_mentions') or []:
        if config.USER_RE.fullmatch(name) and leads.get(name, (0,))[0] < REPLY_MENTION_LEAD:
            leads[name] = (REPLY_MENTION_LEAD, 'reply_mention')
    for name in post.get('commenters') or []:
        if config.USER_RE.fullmatch(name) and name not in leads:
            leads[name] = (COMMENTER_LEAD, 'commenter')
    return leads


def candidate_leads(post):
    """Tags are leads for inspection, not proof of topic relevance."""
    return {name: strength for name, (strength, _) in candidate_lead_kinds(post).items()}


def related_leads(owners, *, include_liked=False, reasons=None, topics=None):
    """Persist broad leads with weaker sponsor evidence, scoped to sources.

    When a dict is passed as reasons, it receives each lead's strongest
    evidence, for example {'source': 'credit', 'via': 'owner', 'liked': False}."""
    owners = set(owners)
    if not owners and not include_liked:
        return {}
    leads = {}
    evidence = {}
    with database() as connection:
        rows = connection.execute('SELECT r.data, f.action FROM reels r LEFT JOIN feedback f ON r.code=f.code '
                                  "WHERE COALESCE(f.action, '') != 'dislike' ORDER BY r.updated DESC LIMIT 3000"
                                  ).fetchall()
        posts = [(json.loads(raw), action) for raw, action in rows]
        anchors = []
        for post, action in posts:
            # A single explicitly liked reel can supply leads. The prospective
            # creator still has to pass the normal multi-post topic checks.
            liked_match = action == 'like' and (include_liked or any(
                re.search(r'\b(?:' + TOPIC_WORDS.get(topic, r'(?!)') + r')\b',
                          post.get('caption') or '', re.I) for topic in (topics or {})))
            if post.get('owner') in owners or owners.intersection(post.get('coauthors') or []) or liked_match:
                anchors.append((post, action))
                for name, (strength, kind) in candidate_lead_kinds(post).items():
                    if strength > leads.get(name, 0):
                        leads[name] = strength
                        evidence[name] = {'source': kind, 'via': post.get('owner'),
                                          'liked': action == 'like'}
        for name, (strength, tags) in _tag_leads(connection, anchors, owners).items():
            if strength > leads.get(name, 0):
                leads[name] = strength
                evidence[name] = {'source': 'hashtag', 'tags': tags}
    if reasons is not None:
        reasons.update({name: evidence[name] for name in leads if name not in owners})
    return {name: strength for name, strength in leads.items() if name not in owners}


def _generic_tags(connection, tags):
    """Tags spread across too many creators to say anything about a topic."""
    if not tags:
        return set()
    total = connection.execute("SELECT COUNT(DISTINCT owner) FROM tag_index WHERE owner != ''").fetchone()[0]
    limit = max(GENERIC_TAG_MIN_CREATORS, GENERIC_TAG_CREATOR_SHARE * total)
    placeholders = ','.join('?' for _ in tags)
    spread = connection.execute(f'SELECT tag, COUNT(DISTINCT owner) FROM tag_index WHERE tag IN ({placeholders}) '
                                'GROUP BY tag', list(tags)).fetchall()
    return {tag for tag, owners in spread if owners >= limit} | (set(tags) & (GENERIC_TAGS | STOP_WORDS))


def _tag_leads(connection, posts, owners):
    """Creators whose stored reels share hashtags with saved/liked reels.

    Instagram serves no logged-out hashtag pages; this works from captions the
    app already fetched. A shared specific tag is a weaker lead than an explicit
    mention, and generic tags carry no evidence at all. Returns
    {owner: (strength, shared tags)}."""
    counts = Counter()
    for post, action in posts:
        if action == 'like' or post.get('owner') in owners:
            for tag in caption_tags(post.get('caption')):
                counts[tag] += LIKED_TAG_WEIGHT if action == 'like' else 1
    generic = _generic_tags(connection, list(counts))
    # The most used specific tags, not the alphabetically first ones.
    anchor_tags = [tag for tag, _ in counts.most_common() if tag not in generic][:MAX_ANCHOR_TAGS]
    if not anchor_tags:
        return {}
    placeholders = ','.join('?' for _ in anchor_tags)
    shared = {}
    for owner, tag in connection.execute(
            f"SELECT DISTINCT t.owner, t.tag FROM tag_index t LEFT JOIN feedback f ON f.code = t.code "
            f"WHERE t.tag IN ({placeholders}) AND t.owner != '' AND COALESCE(f.action, '') != 'dislike'",
            anchor_tags):
        shared.setdefault(owner, []).append(tag)
    rank = {tag: i for i, tag in enumerate(anchor_tags)}
    leads = {}
    for owner, tags in sorted(shared.items(), key=lambda item: -len(item[1]))[:MAX_TAG_LEADS]:
        if owner in owners or not config.USER_RE.fullmatch(owner):
            continue
        tags.sort(key=rank.get)
        leads[owner] = (min(TAG_LEAD_MAX, TAG_LEAD_BASE + TAG_LEAD_STEP * (len(tags) - 1)), tags[:TAGS_SHOWN_IN_REASON])
    return leads


def liked_tags(limit=40):
    """Hashtags of liked reels, strongest first, with their like counts."""
    counts = Counter()
    for reel in liked_reels():
        for tag in caption_tags(reel.get('caption')):
            if tag not in STOP_WORDS:
                counts[tag] += 1
    return counts.most_common(limit)


def top_tags(limit=30):
    """Most seen local hashtags, with creator counts."""
    with database() as connection:
        return connection.execute('SELECT tag, COUNT(DISTINCT owner), COUNT(*) '
                                  'FROM tag_index GROUP BY tag '
                                  'ORDER BY COUNT(*) DESC LIMIT ?', (limit,)).fetchall()


def tag_reels(tag, limit=60):
    """Locally stored reels using a hashtag, newest first, with feedback."""
    with database() as connection:
        rows = connection.execute(
            'SELECT t.code, t.owner, t.updated, COALESCE(f.action, \'\') '
            'FROM tag_index t LEFT JOIN feedback f ON f.code = t.code '
            'WHERE t.tag = ? ORDER BY t.updated DESC LIMIT ?', (tag.lower(), limit)).fetchall()
    return [{'shortcode': code, 'owner': owner, 'updated': updated, 'feedback': action or None}
            for code, owner, updated, action in rows]


def related_accounts(owners):
    return set(related_leads(owners))


# Fan and cinematic edits, not studios or clip channels: "movie" or "film"
# alone is not evidence, so only edit words count.
EDIT_WORDS = (r'edits?|editors?|editing|(?:movie|film|fan|scene|velocity|cinematic|cinema)edits?|'
              r'velocity|after ?effects|ae|capcut|amv|scene ?packs?')


TOPIC_WORDS = {
    'music': r'music|songs?|singers?|albums?|musicians?|concerts?|vocals?|tiny desk|tinydesk|newmusic|livemusic|soul|jazz|hip hop|rnb|r&b',
    'memes': r'memes?|comedy|comedian|humou?r|satire|satirical|shitpost(?:ing)?|brainrot',
    'programming': r'programming|coding|coder|developer|software|computer science|python|javascript',
    'electronics': r'electronics?|circuits?|microcontrollers?|arduino|raspberry pi|soldering|pcb|robotics',
    'islam': r"islam(?:ic)?|muslims?|qur[’']?an|hadith|tafsir|sunnah|salah|ramadan|dhikr|dua|dawah",
    'movie_edits': EDIT_WORDS,
    'edits': EDIT_WORDS + r'|(?:car|bike|motorcycle|automotive|auto|moto) ?edits?|car cinematics?|automotive cinematography',
    'gaming': (r'gaming|gamers?|video ?games?|e-?sports?|twitch|streamers?|gameplay|playstation|ps[45]|'
               r'xbox|nintendo|pc gaming|speedrun(?:s|ner|ners|ning)?'),
    'gym': r'gym|fitness|bodybuilding|powerlifting|weightlifting|strength training',
}


VOCABULARY_TTL = 5 * 60
_vocabulary_cache = {}


def topic_tag_vocabulary(selected):
    """Hashtag vocabulary per feed topic, learned from its own accounts.

    Tags that dominate one topic's stored reels qualify for it, so #gymmemes
    recruits gym candidates even though the word rules cannot see inside the
    tag. Broad tags spread across topics stay weak through the dominance rule,
    and reach-bait tags used by many creators never qualify.

    The result changes slowly, so it is cached for a few minutes instead of
    scanning the whole index for every batch."""
    selected = selected or {}
    key = (str(config.CONFIG_DIR), tuple(sorted((name, tuple(sorted(accounts)))
                                                for name, accounts in selected.items())))
    hit = _vocabulary_cache.get(key)
    if hit and time.time() - hit[0] < VOCABULARY_TTL:
        return {topic: set(tags) for topic, tags in hit[1].items()}
    vocabulary = _build_vocabulary(selected)
    _vocabulary_cache.clear()  # Only the current selection is worth keeping.
    _vocabulary_cache[key] = (time.time(), vocabulary)
    return {topic: set(tags) for topic, tags in vocabulary.items()}


def _build_vocabulary(selected):
    owners = {}
    for name, accounts in selected.items():
        for account in accounts:
            owners.setdefault(account, set()).add(name)  # An account can be in several feeds.
    if not owners:
        return {}
    counts = {}
    with database() as connection:
        placeholders = ','.join('?' for _ in owners)
        for tag, owner, n in connection.execute(
                f'SELECT tag, owner, COUNT(*) FROM tag_index WHERE owner IN ({placeholders}) '
                'GROUP BY tag, owner', list(owners)):
            for topic in owners[owner]:
                counts.setdefault(tag, {}).setdefault(topic, 0)
                counts[tag][topic] += n
        generic = _generic_tags(connection, list(counts))
    vocabulary = {topic: set() for topic in selected}
    for tag, per_topic in counts.items():
        if tag in generic:
            continue
        best = max(per_topic.values())
        for topic, n in per_topic.items():
            if n >= 2 and n >= 0.6 * best:
                vocabulary.setdefault(topic, set()).add(tag)
    return vocabulary


def topic_evidence(state, name, vocabulary=None):
    """Why a candidate matches a topic: 'bio', 'captions', or None.

    A self-described topic in the bio or at least three matching recent video
    captions (and at least half of them) is needed, counting both topic words
    and the feed's own learned hashtags."""
    words = TOPIC_WORDS.get(name)
    if not words:
        return None  # Unknown custom categories need explicit saved membership.
    pattern = re.compile(r'\b(?:' + words + r')\b', re.I)
    tags = {'#' + t for t in (vocabulary or {}).get(name, ())}
    tag_pattern = None
    if tags:
        tag_pattern = re.compile(r'(?:' + '|'.join(sorted(
            re.escape(t) for t in tags)) + r')(?![\w])', re.I)

    def found(text):
        return bool(pattern.search(text) or (tag_pattern and tag_pattern.search(text)))

    if found(state.get('biography') or ''):
        return 'bio'
    videos = [p for p in state.get('posts', []) if p.get('is_video')][:12]
    matches = sum(found((p.get('caption') or '')[:3000]) for p in videos)
    return 'captions' if matches >= 3 and matches >= len(videos) / 2 else None


def matching_topics(state, names, vocabulary=None):
    """Conservative candidate check. Saved membership bypasses this heuristic.

    This cannot understand visual irony and is therefore never used to reject
    the user's own saved accounts. See topic_evidence for the rule."""
    return {name for name in names if topic_evidence(state, name, vocabulary)}


def features(post):
    caption = (post.get('caption') or '').lower()
    tags = {'tag:' + tag for tag in re.findall(r'#(\w+)', caption)
            if tag not in STOP_WORDS}
    text = re.sub(r'https?://\S+|[@#]\w+', ' ', caption)
    words = {'word:' + word for word in re.findall(r'\b[a-z]{4,}\b', text)
             if word not in STOP_WORDS}
    result = set(sorted(words)[:40]) | set(sorted(tags)[:20])
    if post.get('audio_id'):
        result.add('audio:' + str(post['audio_id']))
    song = post.get('song') or {}
    # Original sounds are already covered by the audio id; a licensed track is
    # one song across all its uploads.
    if isinstance(song, dict) and song.get('title') and not song.get('original'):
        result.add('song:' + song['title'].casefold() + ':' + song.get('artist', '').casefold())
    return result


def preference_model():
    topics, creators = Counter(), Counter()
    with database() as connection:
        rows = connection.execute('SELECT r.data, f.action FROM feedback f JOIN reels r '
                                  'ON r.code=f.code ORDER BY f.updated DESC LIMIT 1000').fetchall()
    for raw, action in rows:
        post = json.loads(raw)
        strength = {'like': 3.0, 'dislike': -4.0, 'skip': -0.12}.get(action, 0)
        tokens = features(post)
        for token in tokens:
            topics[token] += strength / math.sqrt(max(1, len(tokens)))
        # Creator preference stays weak: one good reel does not endorse a whole profile.
        if post.get('owner'):
            creators[post['owner']] += strength * 0.12
    return ({key: max(-5, min(5, value)) for key, value in topics.items()},
            {key: max(-1, min(1, value)) for key, value in creators.items()})


def score(post, model):
    topics, creators = model
    tokens = features(post)
    return sum(topics.get(token, 0) for token in tokens) / math.sqrt(max(1, len(tokens))) + creators.get(post.get('owner'), 0)


def liked_features(post, model, limit=TAGS_SHOWN_IN_REASON):
    """This reel's features that most resemble reels you liked."""
    topics = model[0]
    weights = [(topics.get(token, 0), token) for token in features(post)]
    return [token for weight, token in sorted(weights, reverse=True) if weight >= LIKED_FEATURE_MIN_WEIGHT][:limit]


MIN_VIEW_SAMPLES = 5
MAX_VIEW_BOOST = 0.15


def view_baselines(posts):
    """Median views per creator among distinct loaded reels, not lifetime averages."""
    samples = {}
    for post in posts:
        owner, code, count = post.get('owner'), post.get('shortcode'), post.get('play_count')
        if not owner or not code or type(count) is not int or count < 0:
            continue
        samples.setdefault(owner, {})[code] = count
    return {owner: median(counts.values()) for owner, counts in samples.items()
            if len(counts) >= MIN_VIEW_SAMPLES}


def view_count_boost(post, baselines):
    """Small positive tie-breaker; absent, invalid and below-median views are neutral."""
    count = post.get('play_count')
    baseline = baselines.get(post.get('owner'))
    if type(count) is not int or count <= 0 or baseline is None or baseline <= 0 or count <= baseline:
        return 0.0
    # Doubling gives half the maximum; four times the median reaches the cap.
    return min(MAX_VIEW_BOOST, MAX_VIEW_BOOST * (math.log2(count) - math.log2(baseline)) / 2)


def pick_reel(posts, model, *, view_reference=None):
    """Return (reel, exploratory). One fifth of picks are random on purpose."""
    # Keep some selections exploratory, including topics not yet liked.
    if random.random() < EXPLORATORY_SHARE:
        return random.choice(posts), True
    baselines = view_baselines(posts if view_reference is None else view_reference)
    return max(random.sample(posts, len(posts)),
               key=lambda post: score(post, model) + view_count_boost(post, baselines)), False


OLDER_REEL_SHARE = 0.30


def pick_feed_reel(posts, model, *, view_reference=None):
    """Choose a page-age group, then rank within it using the normal preferences.

    Older means beyond the latest listing page, not a particular calendar age.
    The split is a starting experiment, and an empty group never delays playback.
    Callers must remove watched, reserved and disliked reels before this choice.
    """
    older = [post for post in posts if post.get('reel_age_group') == 'older']
    recent = [post for post in posts if post.get('reel_age_group') != 'older']
    if older and recent:
        candidates = older if random.random() < OLDER_REEL_SHARE else recent
    else:
        candidates = older or recent
    return pick_reel(candidates, model, view_reference=view_reference)


def choose_reel(posts, model):
    return pick_reel(posts, model)[0]


def recent_creators():
    with database() as connection:
        rows = connection.execute('SELECT owner FROM viewed_reels ORDER BY viewed DESC, rowid DESC LIMIT 8').fetchall()
    return [row[0] for row in rows]


def recent_view_counts(limit=20):
    """How many of the last `limit` viewed reels each creator supplied."""
    from collections import Counter
    with database() as connection:
        rows = connection.execute('SELECT owner FROM viewed_reels ORDER BY viewed DESC, rowid DESC LIMIT ?', (limit,)).fetchall()
    return Counter(row[0] for row in rows)


def trial_cooldowns(saved):
    """An unfamiliar creator gets one trial per 20 actually viewed reels until liked."""
    with database() as connection:
        rows = connection.execute('SELECT owner FROM viewed_reels ORDER BY viewed DESC, rowid DESC LIMIT 20').fetchall()
        liked = {json.loads(row[0]).get('owner') for row in connection.execute(
            'SELECT r.data FROM reels r JOIN feedback f ON f.code=r.code WHERE f.action=\'like\'')}
    return {row[0] for row in rows} - saved - liked


def record_deliveries(posts):
    with database() as connection:
        connection.executemany('INSERT INTO deliveries (code, owner, sent) VALUES (?, ?, ?)',
                               [(post['shortcode'], post.get('owner') or '', time.time()) for post in posts])
        connection.execute('DELETE FROM deliveries WHERE id NOT IN '
                           '(SELECT id FROM deliveries ORDER BY id DESC LIMIT 200)')


def log_watches(entries):
    """Add many (token, code, owner, seconds, viewed) views to the watch log in
    one transaction, with feeds read once (used to fill it from older history)."""
    if not entries:
        return
    feeds = config.load_feeds()
    feed_of = {}
    for name, accounts in feeds.items():
        for account in config.accounts_from_feed(accounts):
            feed_of.setdefault(account, name)
    with database() as connection:
        codes = list({entry[1] for entry in entries})
        posts = {}
        for i in range(0, len(codes), 500):
            chunk = codes[i:i + 500]
            marks = ','.join('?' for _ in chunk)
            posts.update((code, json.loads(raw)) for code, raw in
                         connection.execute(f'SELECT code, data FROM reels WHERE code IN ({marks})', chunk))
        rows = []
        for token, code, owner, seconds, viewed in entries:
            post = posts.get(code, {})
            feed = feed_of.get(owner)
            rows.append((token, code, owner, viewed or time.time(), seconds, 'feed' if feed else
                         (post.get('discovery_source') or 'other'), feed,
                         json.dumps(sorted(caption_tags(post.get('caption') or ''))[:12])))
        connection.executemany('INSERT OR IGNORE INTO watch_log VALUES (?,?,?,?,?,?,?,?)', rows)


def log_watch(token, code, owner, seconds, viewed=None):
    """Add or update one reel in the permanent watch log (stats page).

    Where the reel came from and its creator's feed are fixed when it is first
    logged, so later feed changes don't rewrite your history."""
    with database() as connection:
        row = connection.execute('SELECT seconds FROM watch_log WHERE token=?', (token,)).fetchone()
        if row:
            if seconds > row[0]:
                connection.execute('UPDATE watch_log SET seconds=? WHERE token=?', (seconds, token))
            return
        data = connection.execute('SELECT data FROM reels WHERE code=?', (code,)).fetchone()
        post = json.loads(data[0]) if data else {}
        feeds = config.load_feeds()
        feed = next((name for name, accounts in feeds.items() if owner in config.accounts_from_feed(accounts)), None)
        source = 'feed' if feed else (post.get('discovery_source') or 'other')
        connection.execute('INSERT INTO watch_log VALUES (?,?,?,?,?,?,?,?)',
                           (token, code, owner, viewed or time.time(), seconds, source, feed,
                            json.dumps(sorted(caption_tags(post.get('caption') or ''))[:12])))


def record_view(code, owner, event):
    with database() as connection:
        connection.execute('INSERT OR IGNORE INTO viewed_reels VALUES (?,?,?,?)', (event, code, owner, time.time()))
        connection.execute('DELETE FROM viewed_reels WHERE event NOT IN (SELECT event FROM viewed_reels ORDER BY viewed DESC, rowid DESC LIMIT 200)')


def discovery_results():
    """Trials actually watched in the retained 200-view window, by source.

    Only explicit likes/dislikes are learning rewards; auto-play completion
    and passive exposure never count as approval. Repeated reports dedupe.
    """
    with database() as connection:
        rows = connection.execute("SELECT DISTINCT v.code, r.data, f.action FROM viewed_reels v "
            "JOIN reels r ON r.code=v.code LEFT JOIN feedback f ON f.code=v.code").fetchall()
    results = {}
    for code, raw, action in rows:
        post = json.loads(raw)
        source = post.get('discovery_source')
        if not source:
            continue
        entry = results.setdefault(source, {'watched':0, 'likes':0, 'dislikes':0, 'creators':set()})
        entry['watched'] += 1
        entry['creators'].add(post.get('owner'))
        entry['likes'] += action == 'like'
        entry['dislikes'] += action == 'dislike'
    return {source: {**entry, 'creators':len(entry['creators'])} for source, entry in results.items()}


def discovery_weights(reasons):
    """Smoothed source rewards with an exploration floor; no passive rewards."""
    results = discovery_results()
    weights = {}
    for name, reason in reasons.items():
        row = results.get(reason.get('source'), {})
        likes, dislikes = row.get('likes', 0), row.get('dislikes', 0)
        # Keep equal chances until there is some explicit evidence.
        weights[name] = 1.0 if likes + dislikes < 8 else 0.25 + 1.5 * (likes + 2) / (likes + dislikes + 4)
    return weights


def saved_streak(saved, queued=()):
    """Count saved creators since the last trial, including upcoming leases."""
    with database() as connection:
        owners = [r[0] for r in connection.execute(
            'SELECT owner FROM viewed_reels ORDER BY viewed DESC, rowid DESC LIMIT 4')]
    streak = 0
    for owner in owners:
        if owner not in saved:
            break
        streak += 1
    for owner in queued:
        streak = streak + 1 if owner in saved else 0
    return streak
