"""Viewed reels, preserved legacy deliveries, and expiring queue reservations."""
from .migrations import migrate
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from .config import CACHE

HISTORY_JSON = CACHE / 'history.json'
MAX_HISTORY = 5000


def _initial_schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS reels (position INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)')
    if not db.execute("SELECT 1 FROM metadata WHERE key = 'migrated'").fetchone():
        try:
            data = json.loads(HISTORY_JSON.read_text())
        except FileNotFoundError:
            data = {'codes': [], 'last_owner': None}
        if not isinstance(data, dict) or not isinstance(data.get('codes'), list):
            raise ValueError('Invalid legacy history; original file was left untouched')
        db.executemany('INSERT OR IGNORE INTO reels(code) VALUES (?)',
                       [(code,) for code in data['codes'][-MAX_HISTORY:] if isinstance(code, str)])
        db.execute("INSERT INTO metadata VALUES ('last_owner', ?)", (data.get('last_owner'),))
        db.execute("INSERT INTO metadata VALUES ('migrated', '1')")


RESERVATION_TTL = 120


def _viewing_schema(db):
    if 'kind' not in {r[1] for r in db.execute('PRAGMA table_info(reels)')}:
        db.execute("ALTER TABLE reels ADD COLUMN kind TEXT NOT NULL DEFAULT 'legacy_delivery'")
    db.execute('CREATE TABLE IF NOT EXISTS reservations (code TEXT PRIMARY KEY, token TEXT UNIQUE NOT NULL, client TEXT NOT NULL, owner TEXT NOT NULL, expires REAL NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS viewings (token TEXT PRIMARY KEY, code TEXT NOT NULL, client TEXT NOT NULL, owner TEXT NOT NULL, viewed REAL NOT NULL, seconds REAL NOT NULL DEFAULT 0)')
    db.execute('CREATE INDEX IF NOT EXISTS reservations_client ON reservations(client)')
    # A receipt authenticates delayed playback reports after a lease is released
    # or expires (e.g. going back to an unplayed reel). It does not reserve it.
    db.execute('CREATE TABLE IF NOT EXISTS queue_receipts (token TEXT PRIMARY KEY, code TEXT NOT NULL, client TEXT NOT NULL, owner TEXT NOT NULL, issued REAL NOT NULL)')


MIGRATIONS = (_initial_schema, _viewing_schema)


@contextmanager
def database():
    path = HISTORY_JSON.with_suffix('.sqlite3')
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    try:
        db.execute('BEGIN IMMEDIATE')
        migrate(db, MIGRATIONS)
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def load_history() -> dict:
    with database() as db:
        return {'codes': [row[0] for row in db.execute('SELECT code FROM reels ORDER BY position')],
                'last_owner': db.execute("SELECT value FROM metadata WHERE key = 'last_owner'").fetchone()[0]}


def save_history(history: dict, posts: list[dict]) -> None:
    if not posts:
        return
    with database() as db:
        db.executemany('INSERT OR IGNORE INTO reels(code) VALUES (?)',
                       [(code,) for code in history['codes'] + [p['shortcode'] for p in posts]])
        db.execute('DELETE FROM reels WHERE position NOT IN (SELECT position FROM reels ORDER BY position DESC LIMIT ?)', (MAX_HISTORY,))
        db.execute("UPDATE metadata SET value = ? WHERE key = 'last_owner'", (posts[0].get('owner'),))


def active_reservations():
    with database() as db:
        db.execute('DELETE FROM reservations WHERE expires <= ?', (time.time(),))
        return db.execute('SELECT code, owner, client FROM reservations ORDER BY rowid').fetchall()


def reserve(posts, client):
    accepted = []
    now = time.time()
    with database() as db:
        db.execute('DELETE FROM reservations WHERE expires <= ?', (now,))
        for post in posts:
            token = uuid.uuid4().hex
            cursor = db.execute('INSERT OR IGNORE INTO reservations(code,token,client,owner,expires) '
                'SELECT ?,?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM reels WHERE code=?)',
                (post['shortcode'], token, client, post.get('owner') or '', now + RESERVATION_TTL, post['shortcode']))
            if cursor.rowcount:
                db.execute('INSERT INTO queue_receipts VALUES (?,?,?,?,?)',
                           (token, post['shortcode'], client, post.get('owner') or '', now))
                post['reservation'] = token
                accepted.append(post)
        db.execute('DELETE FROM queue_receipts WHERE issued < ?', (now - 86400,))
        db.execute('DELETE FROM queue_receipts WHERE token NOT IN (SELECT token FROM queue_receipts ORDER BY issued DESC, rowid DESC LIMIT ?)', (MAX_HISTORY,))
    return accepted


def release(client, tokens=None):
    with database() as db:
        if tokens is None:
            db.execute('DELETE FROM reservations WHERE client=?', (client,))
        else:
            db.executemany('DELETE FROM reservations WHERE client=? AND token=?', [(client, token) for token in tokens])


def renew(client, tokens):
    with database() as db:
        db.executemany('UPDATE reservations SET expires=? WHERE client=? AND token=? AND expires>?',
                      [(time.time() + RESERVATION_TTL, client, token, time.time()) for token in tokens])


def record_playback(code, token, client, seconds):
    """Idempotent cumulative progress for a reserved clip; return its owner."""
    now = time.time()
    with database() as db:
        viewed = db.execute('SELECT owner FROM viewings WHERE token=? AND code=? AND client=?',
                            (token, code, client)).fetchone()
        if viewed:
            db.execute('UPDATE viewings SET seconds=MAX(seconds, ?) WHERE token=?', (seconds, token))
            return viewed[0]
        lease = db.execute('SELECT owner FROM queue_receipts WHERE token=? AND code=? AND client=? AND issued>?',
                           (token, code, client, now - 86400)).fetchone()
        if lease is None:
            return None
        owner = lease[0]
        db.execute('INSERT INTO viewings VALUES (?,?,?,?,?,?)', (token, code, client, owner, now, seconds))
        db.execute("INSERT INTO reels(code,kind) VALUES (?, 'viewed') ON CONFLICT(code) DO UPDATE SET kind='viewed'", (code,))
        db.execute('DELETE FROM reservations WHERE code=?', (code,))
        db.execute("UPDATE metadata SET value=? WHERE key='last_owner'", (owner,))
        db.execute('DELETE FROM reels WHERE position NOT IN (SELECT position FROM reels ORDER BY position DESC LIMIT ?)', (MAX_HISTORY,))
        db.execute('DELETE FROM viewings WHERE token NOT IN (SELECT token FROM viewings ORDER BY viewed DESC LIMIT ?)', (MAX_HISTORY,))
        return owner


def recent_owners(client, limit=2):
    """Actually watched creators for this tab, oldest first."""
    with database() as db:
        rows = db.execute('SELECT owner FROM viewings WHERE client=? ORDER BY viewed DESC, rowid DESC LIMIT ?',
                          (client, limit)).fetchall()
    return [row[0] for row in reversed(rows)]
