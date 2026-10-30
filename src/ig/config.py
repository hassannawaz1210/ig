import json
import os
import re
from pathlib import Path


CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "ig"


SERVER_JSON = CACHE / "server.json"


CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "ig"


FEEDS_JSON = CONFIG_DIR / "feeds.json"


FEED_RE = re.compile(r"^[a-z0-9_-]{1,20}$")


DEFAULT_FEEDS = {
    "edits": [
        "imdb", "netflix", "marvelstudios", "movieclips",
        "cinemaedits", "cinematicedits",
    ],
    "memes": ["memes", "funnymemes", "9gag", "ladbible", "unilad"],
}


def load_feeds() -> dict:
    if FEEDS_JSON.exists():
        try:
            return json.loads(FEEDS_JSON.read_text())
        except json.JSONDecodeError:
            pass
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    FEEDS_JSON.write_text(json.dumps(DEFAULT_FEEDS, indent=2))
    return DEFAULT_FEEDS


def save_feeds(feeds: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    FEEDS_JSON.write_text(json.dumps(feeds, indent=2))


def accounts_from_feed(value) -> list[str]:
    """Read both legacy account lists and saved feed objects; ignore weights."""
    return list(value.get("accounts") or []) if isinstance(value, dict) else list(value or [])

USER_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")


def prefer_memes() -> bool:
    try:
        settings = json.loads((CONFIG_DIR / "preferences.json").read_text())
        return isinstance(settings, dict) and settings.get("prefer_memes") is True
    except (FileNotFoundError, json.JSONDecodeError):
        return False


def set_prefer_memes(enabled: bool) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # Replace the complete file so concurrent readers never see partial JSON.
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", dir=CONFIG_DIR, delete=False) as file:
        json.dump({"prefer_memes": enabled}, file)
        temporary = Path(file.name)
    temporary.replace(CONFIG_DIR / "preferences.json")


# Serialize read/modify/write operations from the threaded HTTP server.
from threading import Lock
BLOCKLIST_LOCK = Lock()


def blocked_accounts() -> set[str]:
    try:
        values = json.loads((CONFIG_DIR / 'blocked.json').read_text())
    except FileNotFoundError:
        return set()
    if not isinstance(values, list):
        raise ValueError('blocked.json must contain a list of usernames')
    return {v.lower() for v in values if isinstance(v, str) and USER_RE.fullmatch(v)}


def set_blocked(username: str, blocked: bool) -> set[str]:
    username = username.lstrip('@').lower()
    if not USER_RE.fullmatch(username):
        raise ValueError('invalid username')
    with BLOCKLIST_LOCK:
        accounts = blocked_accounts()
        accounts.add(username) if blocked else accounts.discard(username)
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', dir=CONFIG_DIR, delete=False) as file:
            json.dump(sorted(accounts), file, indent=2)
            temporary = Path(file.name)
        temporary.replace(CONFIG_DIR / 'blocked.json')
        return accounts
