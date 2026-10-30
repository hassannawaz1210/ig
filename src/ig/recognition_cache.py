"""Reuse song matches without repeatedly submitting the same reel."""
import json
import sqlite3
import time
from .config import CACHE
from .migrations import migrate

DATABASE = CACHE / 'songs.sqlite3'
MATCH_TTL = 30 * 24 * 60 * 60
NO_MATCH_TTL = 24 * 60 * 60


def _connect():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE, timeout=10)
    try:
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            migrate(connection, (_initial_schema,))
        return connection
    except BaseException:
        connection.close()
        raise


def _initial_schema(connection):
    connection.execute('CREATE TABLE IF NOT EXISTS songs '
                       '(code TEXT PRIMARY KEY, result TEXT NOT NULL, expires REAL NOT NULL)')


def cached_song(code):
    connection = _connect()
    try:
        row = connection.execute('SELECT result FROM songs WHERE code = ? AND expires > ?',
                                 (code, time.time())).fetchone()
        return json.loads(row[0]) if row else None
    finally:
        connection.close()


def cache_song(code, result):
    if result.get('status') not in {'matched', 'no_match'}:
        return
    ttl = MATCH_TTL if result['status'] == 'matched' else NO_MATCH_TTL
    connection = _connect()
    try:
        with connection:
            connection.execute('DELETE FROM songs WHERE expires <= ?', (time.time(),))
            connection.execute('INSERT OR REPLACE INTO songs VALUES (?, ?, ?)',
                               (code, json.dumps(result), time.time() + ttl))
            connection.execute('DELETE FROM songs WHERE code NOT IN '
                               '(SELECT code FROM songs ORDER BY expires DESC LIMIT 5000)')
    finally:
        connection.close()
