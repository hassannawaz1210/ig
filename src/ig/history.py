"""Bounded history of reels delivered to the browser, across server restarts."""
import json
import tempfile
from .config import CACHE

HISTORY_JSON = CACHE / 'history.json'
MAX_HISTORY = 5000


def load_history() -> dict:
    try:
        data = json.loads(HISTORY_JSON.read_text())
        if isinstance(data, dict) and isinstance(data.get('codes'), list):
            return {'codes': [c for c in data['codes'] if isinstance(c, str)][-MAX_HISTORY:],
                    'last_owner': data.get('last_owner')}
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {'codes': [], 'last_owner': None}


def save_history(history: dict, posts: list[dict]) -> None:
    if not posts:
        return
    codes = list(dict.fromkeys(history['codes'] + [p['shortcode'] for p in posts]))
    data = {'codes': codes[-MAX_HISTORY:], 'last_owner': posts[0].get('owner')}
    HISTORY_JSON.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=HISTORY_JSON.parent, delete=False) as file:
        json.dump(data, file)
        temporary = file.name
    from pathlib import Path
    Path(temporary).replace(HISTORY_JSON)
