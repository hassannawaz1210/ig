from .migrations import migrate
import json
import os
import re
from pathlib import Path


CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "ig"


SERVER_JSON = CACHE / "server.json"

# The embedded Android runtime sets this before importing ig.
ANDROID = os.environ.get("IG_ANDROID") == "1"


CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "ig"


FEED_RE = re.compile(r"^[a-z0-9_-]{1,20}$")


DEFAULT_FEEDS = {}  # New users create their own feeds.


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


# Historical cleanup retained for databases that have not reached version 3.
STUDIO_AND_CLIP_ACCOUNTS = ("imdb", "netflix", "marvelstudios", "movieclips")


def _edits_and_gaming_schema(db):
    """Clean up the old movie feed without adding predefined memberships."""
    db.executemany("DELETE FROM feed_accounts WHERE feed = 'movie_edits' AND username = ?",
                   [(username,) for username in STUDIO_AND_CLIP_ACCOUNTS])


def _rename_edits_schema(db):
    feeds = _read_feeds(db)
    if 'movie_edits' in feeds:
        combined = list(dict.fromkeys(accounts_from_feed(feeds.get('edits')) + accounts_from_feed(feeds['movie_edits'])))
        renamed = {}
        for name, accounts in feeds.items():
            renamed['edits' if name == 'movie_edits' else name] = combined if name in {'edits', 'movie_edits'} else accounts
        _write_feeds(db, renamed)
    row = db.execute("SELECT value FROM text_preferences WHERE key='selected_feeds'").fetchone()
    if row:
        selection = json.loads(row[0])
        if selection is not None:
            selection = sorted(set('edits' if name == 'movie_edits' else name for name in selection))
            db.execute("UPDATE text_preferences SET value=? WHERE key='selected_feeds'", (json.dumps(selection),))


def _deleted_feeds_schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS deleted_feeds (id INTEGER PRIMARY KEY, name TEXT NOT NULL, accounts TEXT NOT NULL, deleted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, restored INTEGER NOT NULL DEFAULT 0)')


SETTINGS_MIGRATIONS = (_initial_schema, _feed_selection_schema, _edits_and_gaming_schema, _rename_edits_schema, _deleted_feeds_schema)


def delete_feed(name):
    with database() as db:
        feeds = _read_feeds(db)
        if name not in feeds:
            raise KeyError(name)
        cursor = db.execute('INSERT INTO deleted_feeds(name, accounts) VALUES (?, ?)',
                            (name, json.dumps(feeds.pop(name))))
        deletion_id = cursor.lastrowid
        _write_feeds(db, feeds)
        return deletion_id


def restore_feed(deletion_id):
    with database() as db:
        row = db.execute('SELECT name, accounts FROM deleted_feeds WHERE id=? AND restored=0', (deletion_id,)).fetchone()
        if row is None:
            raise KeyError(deletion_id)
        name, accounts = row
        feeds = _read_feeds(db)
        feeds[name] = list(dict.fromkeys(accounts_from_feed(feeds.get(name)) + json.loads(accounts)))
        _write_feeds(db, feeds)
        db.execute('UPDATE deleted_feeds SET restored=1 WHERE id=?', (deletion_id,))
        return name


@contextmanager
def database(*, readonly=False):
    """Open a transaction, importing legacy files exactly once, atomically."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(CONFIG_DIR / "settings.sqlite3", timeout=30)
    try:
        db.execute("PRAGMA foreign_keys = ON")
        needs_migration = db.execute("PRAGMA user_version").fetchone()[0] != len(SETTINGS_MIGRATIONS)
        db.execute("BEGIN IMMEDIATE" if not readonly or needs_migration else "BEGIN")
        migrate(db, SETTINGS_MIGRATIONS)
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def load_feeds() -> dict:
    with database(readonly=True) as db:
        return _read_feeds(db)


def save_feeds(feeds: dict) -> None:
    with database() as db:
        _write_feeds(db, feeds)


@contextmanager
def edit_feeds(*, block=(), unblock=()):
    """Keep read/modify/write atomic across CLI and server processes."""
    with database() as db:
        feeds = _read_feeds(db)
        yield feeds
        _write_feeds(db, feeds)
        db.executemany("INSERT OR IGNORE INTO blocked_accounts VALUES (?)",
                       [(username,) for username in block])
        db.executemany("DELETE FROM blocked_accounts WHERE username = ?",
                       [(username,) for username in unblock])


def selected_feeds() -> list[str] | None:
    """None means All; an empty result after deletions deliberately shows none."""
    with database(readonly=True) as db:
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


def scroll_animation() -> str:
    with database(readonly=True) as db:
        row = db.execute("SELECT value FROM text_preferences WHERE key = 'scroll_animation'").fetchone()
        return row[0] if row and row[0] in ('smooth', 'snappy') else 'snappy'


def set_scroll_animation(mode: str) -> None:
    if not isinstance(mode, str) or mode not in ('smooth', 'snappy'):
        raise ValueError('Choose smooth or snappy scrolling.')
    with database() as db:
        db.execute("INSERT INTO text_preferences(key, value) VALUES ('scroll_animation', ?) "
                   "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (mode,))


def _playback_preferences(db, *, include_missing=False) -> dict:
    values = dict(db.execute("SELECT key, value FROM text_preferences WHERE key IN ('sound', 'auto_scroll')"))
    result = {key: values.get(key) == 'true' for key in ('sound', 'auto_scroll')}
    if include_missing:
        result['playback_missing'] = [key for key in ('sound', 'auto_scroll') if key not in values]
    return result


def playback_preferences(*, include_missing=False) -> dict:
    """Playback choices belong to the local profile, including across Android restarts."""
    with database(readonly=True) as db:
        return _playback_preferences(db, include_missing=include_missing)


def set_playback_preferences(changes: dict, *, migrate=False) -> dict:
    if (type(migrate) is not bool or not isinstance(changes, dict) or not changes or
            set(changes) - {'sound', 'auto_scroll'} or
            any(type(value) is not bool for value in changes.values())):
        raise ValueError('Use sound and/or auto_scroll with true or false.')
    with database() as db:
        conflict = "DO NOTHING" if migrate else "DO UPDATE SET value = excluded.value"
        db.executemany("INSERT INTO text_preferences(key, value) VALUES (?, ?) "
                       "ON CONFLICT(key) " + conflict,
                       [(key, json.dumps(value)) for key, value in changes.items()])
        return _playback_preferences(db)


def remove_feed_account(name: str, username: str) -> dict:
    """Remove one membership without blocking the creator or changing other feeds."""
    if not isinstance(name, str) or not FEED_RE.fullmatch(name):
        raise ValueError('Invalid feed.')
    if not isinstance(username, str) or not USER_RE.fullmatch(username):
        raise ValueError('Invalid Instagram handle.')
    username = username.lower()
    with database() as db:
        if db.execute('SELECT 1 FROM feeds WHERE name = ?', (name,)).fetchone() is None:
            raise KeyError(name)
        removed = db.execute('DELETE FROM feed_accounts WHERE feed = ? AND username = ?',
                             (name, username)).rowcount > 0
        accounts = [row[0] for row in db.execute(
            'SELECT username FROM feed_accounts WHERE feed = ? ORDER BY position', (name,))]
    return {'name': name, 'username': username, 'removed': removed,
            'accounts': accounts, 'count': len(accounts)}


def prefer_memes() -> bool:
    with database(readonly=True) as db:
        return bool(db.execute("SELECT value FROM settings WHERE key = 'prefer_memes'").fetchone()[0])


def set_prefer_memes(enabled: bool) -> None:
    with database() as db:
        db.execute("UPDATE settings SET value = ? WHERE key = 'prefer_memes'", (enabled,))


def blocked_accounts() -> set[str]:
    with database(readonly=True) as db:
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
    with database(readonly=True) as db:
        row = db.execute("SELECT value FROM text_preferences WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None


def set_text_preference(key: str, value: str) -> None:
    with database() as db:
        db.execute("INSERT OR REPLACE INTO text_preferences VALUES (?, ?)", (key, value))
