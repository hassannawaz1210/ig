"""Local reel preferences, topic matching, and trial-account discovery."""
import json
import math
import random
import re
import sqlite3
import time
from collections import Counter
from contextlib import contextmanager
from . import config
from .migrations import migrate

STOP_WORDS = set('this that with from your have just when what like follow share viral reels reel instagram explore fyp foryou trending funny video videos meme memes content original audio sound http https www com the and for you are not but'.split())


TAG_RE = re.compile(r'#([\w]{2,40})')


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


@contextmanager
def database():
    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(config.CONFIG_DIR / 'recommendations.sqlite3', timeout=10)
    try:
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            migrate(connection, (_initial_schema, _tag_index_schema))
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
            for key in ('owner', 'caption', 'tagged_users', 'audio_id', 'song'):
                if post.get(key) is not None:
                    data[key] = post[key]
            connection.execute('INSERT OR REPLACE INTO reels VALUES (?, ?, ?)',
                               (code, json.dumps(data), now))
            tags = caption_tags(data.get('caption'))
            if tags:
                owner = data.get('owner') or ''
                connection.executemany('INSERT OR REPLACE INTO tag_index VALUES (?, ?, ?, ?)',
                                       [(tag, code, owner, now) for tag in tags])
        # Keep explicit feedback metadata; bound unreviewed candidates.
        connection.execute('DELETE FROM reels WHERE code NOT IN (SELECT code FROM feedback) '
                           'AND code NOT IN (SELECT code FROM reels ORDER BY updated DESC LIMIT 10000)')
        # The index follows the reels it was built from; feedback keeps its rows.
        # reels is already bounded, which bounds the index with it.
        connection.execute('DELETE FROM tag_index WHERE code NOT IN (SELECT code FROM reels)')


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


def candidate_leads(post):
    """Tags are leads for inspection, not proof of topic relevance."""
    caption = post.get('caption') or ''
    handles = set(post.get('tagged_users') or [])
    handles.update(re.findall(r'@([A-Za-z0-9._]{1,30})', caption))
    promotional = bool(re.search(r'(?i)(#ad\b|#sponsored\b|paid partnership|sponsored by|partnering with|promo code|discount code)', caption))
    leads = {name.rstrip('.').lower(): 0.2 if promotional else 1.0 for name in handles
             if isinstance(name, str) and config.USER_RE.fullmatch(name)}
    for name in creator_credits(post):
        leads[name] = 0.5 if promotional else 2.0
    return leads


def related_leads(owners, *, include_liked=False):
    """Persist broad leads with weaker sponsor evidence, scoped to sources."""
    owners = set(owners)
    if not owners and not include_liked:
        return {}
    leads = {}
    with database() as connection:
        rows = connection.execute('SELECT r.data, f.action FROM reels r LEFT JOIN feedback f ON r.code=f.code '
                                  "WHERE COALESCE(f.action, '') != 'dislike' ORDER BY r.updated DESC LIMIT 3000"
                                  ).fetchall()
        for raw, action in rows:
            post = json.loads(raw)
            if post.get('owner') in owners or (include_liked and action == 'like'):
                for name, strength in candidate_leads(post).items():
                    leads[name] = max(leads.get(name, 0), strength)
        for name, strength in _tag_leads(connection, rows, owners).items():
            leads[name] = max(leads.get(name, 0), strength)
    return {name: strength for name, strength in leads.items() if name not in owners}


def _tag_leads(connection, rows, owners):
    """Creators whose stored reels share hashtags with saved/liked reels.

    Instagram serves no logged-out hashtag pages; this works from captions the
    app already fetched. A shared specific tag is a weaker lead than an explicit
    mention, and generic tags carry no evidence at all."""
    anchor_tags = set()
    for raw, action in rows:
        post = json.loads(raw)
        if action == 'like' or post.get('owner') in owners:
            anchor_tags.update(t for t in caption_tags(post.get('caption'))
                               if t not in STOP_WORDS)
    anchor_tags = sorted(anchor_tags)[:60]
    if not anchor_tags:
        return {}
    placeholders = ','.join('?' for _ in anchor_tags)
    rows2 = connection.execute(
        f'SELECT owner, COUNT(DISTINCT tag) AS shared FROM tag_index '
        f'WHERE tag IN ({placeholders}) AND owner != \'\' GROUP BY owner '
        f'ORDER BY shared DESC LIMIT 60', anchor_tags).fetchall()
    leads = {}
    for owner, shared in rows2:
        if owner in owners or not config.USER_RE.fullmatch(owner):
            continue
        leads[owner] = min(1.5, 0.5 + 0.25 * (shared - 1))
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


def topic_tag_vocabulary(selected):
    """Hashtag vocabulary per feed topic, learned from its own accounts.

    Tags that dominate one topic's stored reels qualify for it, so #gymmemes
    recruits gym candidates even though the word rules cannot see inside the
    tag. Broad tags spread across topics stay weak through the dominance rule."""
    counts = {}
    owners = {}
    for name, accounts in (selected or {}).items():
        owners.update({a: name for a in accounts})
    if not owners:
        return {}
    with database() as connection:
        for tag, owner, n in connection.execute(
                'SELECT tag, owner, COUNT(*) FROM tag_index GROUP BY tag, owner'):
            topic = owners.get(owner)
            if topic:
                counts.setdefault(tag, {}).setdefault(topic, 0)
                counts[tag][topic] += n
    vocabulary = {topic: set() for topic in selected or {}}
    for tag, per_topic in counts.items():
        best = max(per_topic.values())
        for topic, n in per_topic.items():
            if n >= 2 and n >= 0.6 * best:
                vocabulary.setdefault(topic, set()).add(tag)
    return vocabulary


def matching_topics(state, names, vocabulary=None):
    """Conservative candidate check. Saved membership bypasses this heuristic.

    A self-described topic in the bio or at least three matching recent video
    captions is needed, counting both topic words and the feed's own learned
    hashtags. This cannot understand visual irony and is therefore
    never used to reject the user's own saved accounts.
    """
    result = set()
    bio = state.get('biography') or ''
    videos = [p for p in state.get('posts', []) if p.get('is_video')][:12]
    vocabulary = vocabulary or {}
    for name in names:
        words = TOPIC_WORDS.get(name)
        if not words:
            continue  # Unknown custom categories need explicit saved membership.
        pattern = re.compile(r'\b(?:' + words + r')\b', re.I)
        tags = {'#' + t for t in vocabulary.get(name, ())}
        tag_pattern = None
        if tags:
            tag_pattern = re.compile(r'(?:' + '|'.join(sorted(
                re.escape(t) for t in tags)) + r')(?![\w])', re.I)
        matches = 0
        for p in videos:
            caption = (p.get('caption') or '').split('\n\n')[0]
            if pattern.search(caption) or (tag_pattern and tag_pattern.search(caption)):
                matches += 1
        if pattern.search(bio) or (tag_pattern and tag_pattern.search(bio)) \
                or (matches >= 3 and matches >= len(videos) / 2):
            result.add(name)
    return result


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


def choose_reel(posts, model):
    # Keep one fifth of selections exploratory, including topics not yet liked.
    if random.random() < 0.2:
        return random.choice(posts)
    return max(random.sample(posts, len(posts)), key=lambda post: score(post, model))


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
