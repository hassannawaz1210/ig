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


def _initial_schema(connection):
    connection.execute('CREATE TABLE IF NOT EXISTS reels (code TEXT PRIMARY KEY, data TEXT NOT NULL, updated REAL NOT NULL)')
    connection.execute('CREATE TABLE IF NOT EXISTS feedback (code TEXT PRIMARY KEY, action TEXT NOT NULL, updated REAL NOT NULL)')
    connection.execute('CREATE TABLE IF NOT EXISTS deliveries (id INTEGER PRIMARY KEY, code TEXT NOT NULL, owner TEXT NOT NULL, sent REAL NOT NULL)')


@contextmanager
def database():
    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(config.CONFIG_DIR / 'recommendations.sqlite3', timeout=10)
    try:
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            migrate(connection, (_initial_schema,))
            yield connection
    finally:
        connection.close()


def remember_reels(posts):
    """Keep only recommendation metadata, never video URLs or media files."""
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
                               (code, json.dumps(data), time.time()))
        # Keep explicit feedback metadata; bound unreviewed candidates.
        connection.execute('DELETE FROM reels WHERE code NOT IN (SELECT code FROM feedback) '
                           'AND code NOT IN (SELECT code FROM reels ORDER BY updated DESC LIMIT 10000)')


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
    """New creators are leads from liked reels, not people who commented on them."""
    accounts = set()
    for post in liked_reels():
        caption = post.get('caption') or ''
        accounts.update(post.get('tagged_users') or [])
        accounts.update(re.findall(r'@([A-Za-z0-9._]{1,30})', caption))
        # A liked creator gets future trials, without becoming a saved account.
        if post.get('owner'):
            accounts.add(post['owner'])
    return {a.lower() for a in accounts if isinstance(a, str) and config.USER_RE.fullmatch(a)}


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
