"""Bounded SQLite history of delivered reels, across server restarts."""
from .migrations import migrate
import json
import sqlite3
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


@contextmanager
def database():
    path = HISTORY_JSON.with_suffix('.sqlite3')
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    try:
        db.execute('BEGIN IMMEDIATE')
        migrate(db, (_initial_schema,))
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
