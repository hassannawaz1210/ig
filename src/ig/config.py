from .migrations import migrate
import json
import os
import re
from pathlib import Path


CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "ig"


SERVER_JSON = CACHE / "server.json"


CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "ig"


FEED_RE = re.compile(r"^[a-z0-9_-]{1,20}$")


DEFAULT_FEEDS = {
    "movie_edits": [
        "imdb", "netflix", "marvelstudios", "movieclips",
        "cinemaedits", "cinematicedits",
    ],
    "memes": ["memes", "funnymemes", "9gag", "ladbible", "unilad"],
    "electronics": ["adafruit", "sparkfun"],
    "programming": ["cs50", "freecodecamp", "fireship_dev", "codewithharry"],
}


def accounts_from_feed(value) -> list[str]:
    """Accept legacy feed objects during migration; weights are ignored."""
    return list(value.get("accounts") or []) if isinstance(value, dict) else list(value or [])


USER_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")

from contextlib import contextmanager
import sqlite3


def _legacy_json(path, default):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def _read_feeds(db):
    feeds = {name: [] for (name,) in db.execute("SELECT name FROM feeds ORDER BY position")}
    for name, username in db.execute("SELECT feed, username FROM feed_accounts ORDER BY position"):
        feeds[name].append(username)
    return feeds


def _write_feeds(db, feeds):
    db.execute("DELETE FROM feed_accounts")
    db.execute("DELETE FROM feeds")
    for position, (name, value) in enumerate(feeds.items()):
        db.execute("INSERT INTO feeds VALUES (?, ?)", (name, position))
        accounts = dict.fromkeys(accounts_from_feed(value))
        db.executemany("INSERT INTO feed_accounts VALUES (?, ?, ?)",
                       [(name, username, i) for i, username in enumerate(accounts)])


def _initial_schema(db):
    db.execute("CREATE TABLE IF NOT EXISTS feeds (name TEXT PRIMARY KEY, position INTEGER NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS feed_accounts (feed TEXT REFERENCES feeds(name), username TEXT, position INTEGER NOT NULL, PRIMARY KEY (feed, username))")
    db.execute("CREATE TABLE IF NOT EXISTS blocked_accounts (username TEXT PRIMARY KEY)")
    db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value INTEGER NOT NULL)")
    if not db.execute("SELECT 1 FROM settings WHERE key = 'migrated'").fetchone():
        feeds = _legacy_json(CONFIG_DIR / "feeds.json", DEFAULT_FEEDS)
        blocks = _legacy_json(CONFIG_DIR / "blocked.json", [])
        preferences = _legacy_json(CONFIG_DIR / "preferences.json", {})
        if not isinstance(feeds, dict) or not isinstance(blocks, list) or not isinstance(preferences, dict):
            raise ValueError("Invalid legacy settings; original files were left untouched")
        _write_feeds(db, feeds)
        db.executemany("INSERT OR IGNORE INTO blocked_accounts VALUES (?)",
                       [(v.lower(),) for v in blocks if isinstance(v, str) and USER_RE.fullmatch(v)])
        db.execute("INSERT INTO settings VALUES ('prefer_memes', ?)", (preferences.get('prefer_memes') is True,))
        db.execute("INSERT INTO settings VALUES ('migrated', 1)")


def _feed_selection_schema(db):
    db.execute("CREATE TABLE IF NOT EXISTS text_preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    preferred = db.execute("SELECT value FROM settings WHERE key = 'prefer_memes'").fetchone()
    memes_exist = db.execute("SELECT 1 FROM feeds WHERE name = 'memes'").fetchone()
    selection = ['memes'] if preferred and preferred[0] and memes_exist else None
    db.execute("INSERT OR IGNORE INTO text_preferences VALUES ('selected_feeds', ?)", (json.dumps(selection),))


@contextmanager
def database():
    """Open a transaction, importing legacy files exactly once, atomically."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(CONFIG_DIR / "settings.sqlite3", timeout=30)
    try:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("BEGIN IMMEDIATE")
        migrate(db, (_initial_schema, _feed_selection_schema))
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def load_feeds() -> dict:
    with database() as db:
        return _read_feeds(db)


def save_feeds(feeds: dict) -> None:
    with database() as db:
        _write_feeds(db, feeds)


@contextmanager
def edit_feeds():
    """Keep read/modify/write atomic across CLI and server processes."""
    with database() as db:
        feeds = _read_feeds(db)
        yield feeds
        _write_feeds(db, feeds)


def selected_feeds() -> list[str] | None:
    """None means All; an empty result after deletions deliberately shows none."""
    with database() as db:
        selection = json.loads(db.execute("SELECT value FROM text_preferences WHERE key = 'selected_feeds'").fetchone()[0])
        if selection is None:
            return None
        existing = {row[0] for row in db.execute('SELECT name FROM feeds')}
        return [name for name in selection if name in existing]


def set_selected_feeds(names: list[str] | None) -> None:
    if names is not None and (not isinstance(names, list) or not names or
                              any(not isinstance(name, str) for name in names)):
        raise ValueError('Choose one or more feeds, or All')
    with database() as db:
        existing = {row[0] for row in db.execute('SELECT name FROM feeds')}
        if names is not None and any(name not in existing for name in names):
            raise ValueError('Unknown feed')
        selection = None if names is None else sorted(set(names))
        db.execute("UPDATE text_preferences SET value = ? WHERE key = 'selected_feeds'", (json.dumps(selection),))


def prefer_memes() -> bool:
    with database() as db:
        return bool(db.execute("SELECT value FROM settings WHERE key = 'prefer_memes'").fetchone()[0])


def set_prefer_memes(enabled: bool) -> None:
    with database() as db:
        db.execute("UPDATE settings SET value = ? WHERE key = 'prefer_memes'", (enabled,))


def blocked_accounts() -> set[str]:
    with database() as db:
        return {row[0] for row in db.execute("SELECT username FROM blocked_accounts")}


def set_blocked(username: str, blocked: bool) -> set[str]:
    username = username.lstrip('@').lower()
    if not USER_RE.fullmatch(username):
        raise ValueError('invalid username')
    with database() as db:
        if blocked:
            db.execute("INSERT OR IGNORE INTO blocked_accounts VALUES (?)", (username,))
        else:
            db.execute("DELETE FROM blocked_accounts WHERE username = ?", (username,))
        return {row[0] for row in db.execute("SELECT username FROM blocked_accounts")}



def text_preference(key: str) -> str | None:
    with database() as db:
        row = db.execute("SELECT value FROM text_preferences WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None


def set_text_preference(key: str, value: str) -> None:
    with database() as db:
        db.execute("INSERT OR REPLACE INTO text_preferences VALUES (?, ?)", (key, value))
