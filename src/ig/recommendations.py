"""Local reel preferences, topic matching, and trial-account discovery."""
import json
import logging
import math
import random
import re
import sqlite3
import time
from collections import Counter
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


MIGRATIONS = (_initial_schema, _tag_index_schema, _saved_reels_schema)


@contextmanager
def database():
    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(config.CONFIG_DIR / 'recommendations.sqlite3', timeout=10)
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
            for key in ('owner', 'caption', 'tagged_users', 'audio_id', 'song'):
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


def remember_song(code, song):
    if song.get('status') == 'matched':
        remember_reels([{'shortcode': code, 'song': song}])


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


def candidate_lead_kinds(post):
    """Leads from one reel as {name: (strength, kind)}.

    kind is 'credit', 'mention', 'sponsored_credit' or 'sponsored_mention'."""
    caption = post.get('caption') or ''
    handles = set(post.get('tagged_users') or [])
    handles.update(re.findall(r'@([A-Za-z0-9._]{1,30})', caption))
    promotional = bool(PROMOTIONAL_RE.search(caption))
    leads = {name.rstrip('.').lower(): (0.2, 'sponsored_mention') if promotional else (1.0, 'mention')
             for name in handles if isinstance(name, str) and config.USER_RE.fullmatch(name)}
    for name in creator_credits(post):
        leads[name] = (0.5, 'sponsored_credit') if promotional else (2.0, 'credit')
    return leads


def candidate_leads(post):
    """Tags are leads for inspection, not proof of topic relevance."""
    return {name: strength for name, (strength, _) in candidate_lead_kinds(post).items()}


def related_leads(owners, *, include_liked=False, reasons=None):
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
        for post, action in posts:
            if post.get('owner') in owners or (include_liked and action == 'like'):
                for name, (strength, kind) in candidate_lead_kinds(post).items():
                    if strength > leads.get(name, 0):
                        leads[name] = strength
                        evidence[name] = {'source': kind, 'via': post.get('owner'),
                                          'liked': action == 'like'}
        for name, (strength, tags) in _tag_leads(connection, posts, owners).items():
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
    limit = max(8, 0.15 * total)
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
                counts[tag] += 2 if action == 'like' else 1
    generic = _generic_tags(connection, list(counts))
    # The most used specific tags, not the alphabetically first ones.
    anchor_tags = [tag for tag, _ in counts.most_common() if tag not in generic][:60]
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
    for owner, tags in sorted(shared.items(), key=lambda item: -len(item[1]))[:60]:
        if owner in owners or not config.USER_RE.fullmatch(owner):
            continue
        tags.sort(key=rank.get)
        leads[owner] = (min(1.5, 0.5 + 0.25 * (len(tags) - 1)), tags[:3])
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


TOPIC_WORDS = {
    'memes': r'memes?|comedy|comedian|humou?r|satire|satirical|shitpost(?:ing)?|brainrot',
    'programming': r'programming|coding|coder|developer|software|computer science|python|javascript',
    'electronics': r'electronics?|circuits?|microcontrollers?|arduino|raspberry pi|soldering|pcb|robotics',
    'movie_edits': r'movies?|cinema|cinematic|film|films|filmmaker',
    'edits': r'movies?|cinema|cinematic|film|films|filmmaker',
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
    matches = sum(found((p.get('caption') or '').split('\n\n')[0]) for p in videos)
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
    if song.get('title'):
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


def liked_features(post, model, limit=3):
    """This reel's features that most resemble reels you liked."""
    topics = model[0]
    weights = [(topics.get(token, 0), token) for token in features(post)]
    return [token for weight, token in sorted(weights, reverse=True) if weight >= 0.25][:limit]


def pick_reel(posts, model):
    """Return (reel, exploratory). One fifth of picks are random on purpose."""
    # Keep one fifth of selections exploratory, including topics not yet liked.
    if random.random() < 0.2:
        return random.choice(posts), True
    return max(random.sample(posts, len(posts)), key=lambda post: score(post, model)), False


def choose_reel(posts, model):
    return pick_reel(posts, model)[0]


def recent_creators():
    with database() as connection:
        rows = connection.execute('SELECT owner FROM deliveries ORDER BY id DESC LIMIT 8').fetchall()
    return [row[0] for row in rows]


def trial_cooldowns(saved):
    """An unfamiliar creator gets one trial per 20 delivered reels until liked."""
    with database() as connection:
        rows = connection.execute('SELECT owner FROM deliveries ORDER BY id DESC LIMIT 20').fetchall()
        liked = {json.loads(row[0]).get('owner') for row in connection.execute(
            'SELECT r.data FROM reels r JOIN feedback f ON f.code=r.code WHERE f.action=\'like\'')}
    return {row[0] for row in rows} - saved - liked


def record_deliveries(posts):
    with database() as connection:
        connection.executemany('INSERT INTO deliveries (code, owner, sent) VALUES (?, ?, ?)',
                               [(post['shortcode'], post.get('owner') or '', time.time()) for post in posts])
        connection.execute('DELETE FROM deliveries WHERE id NOT IN '
                           '(SELECT id FROM deliveries ORDER BY id DESC LIMIT 200)')
