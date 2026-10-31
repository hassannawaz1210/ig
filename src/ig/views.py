"""Render the profile and reels pages from the templates in ig/static.

Templates use {{name}} for values (escaped by the caller) and {{icon:name}}
for inline SVG icons. CSS and JavaScript are plain files served from /static."""
import json
import re
from functools import lru_cache
from html import escape
from pathlib import Path

from .icons import icon, icons_js
from .config import blocked_accounts, load_feeds, selected_feeds
from .feeds import feed_accounts

STATIC_DIR = Path(__file__).with_name("static")
STATIC_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}
STATIC_NAME = re.compile(r"[a-z][a-z0-9_-]{0,40}\.(?:js|css)")
PLACEHOLDER = re.compile(r"\{\{(icon:)?([a-z_]+)\}\}")


@lru_cache(maxsize=None)
def _template(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def render(name: str, **values: str) -> str:
    def fill(match):
        if match.group(1):
            return icon(match.group(2))
        return values[match.group(2)]  # A missing value is a programming error.
    return PLACEHOLDER.sub(fill, _template(name))


def _page_data(data: dict) -> str:
    """JSON for a <script type="application/json"> block, safe against </script>."""
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def static_file(name: str) -> tuple[bytes, str] | None:
    """Contents and type of a /static file, or None if there is no such file."""
    if name == "icons.js":
        return icons_js().encode(), STATIC_TYPES[".js"]
    if not STATIC_NAME.fullmatch(name):
        return None
    path = STATIC_DIR / name
    if not path.is_file():
        return None
    return path.read_bytes(), STATIC_TYPES[path.suffix]


def viewer_html(title: str, subtitle: str, posts: list[dict],
                username: str = "", paged: bool = True, empty_message: str = "") -> str:
    """The post grid page. paged=False shows every post at once, without a
    Load more button; empty_message is shown when there are no posts."""
    add_button = ""
    if username:
        saved = username in feed_accounts()
        add_button = (f'<button id="addacc" data-state="{"in" if saved else "out"}" '
                      f'onclick="toggleAcc({escape(json.dumps(username), quote=True)}, this)">'
                      + (icon("check") + " in feed" if saved else icon("plus") + " add to feed")
                      + "</button>")
    return render("profile.html", title=escape(title), subtitle=escape(subtitle),
                  add_button=add_button,
                  more_button='<button id="more">Load more</button>' if paged else "",
                  boot=_page_data({"posts": posts, "username": username,
                                   "empty_message": empty_message}))


def reels_html() -> str:
    """The reels page shell. The page fetches its reels after it loads."""
    check = icon("check")
    choices = "".join(
        f'<button type="button" class="feed-choice" data-feed="{escape(name, quote=True)}" '
        f'aria-pressed="false">{check}<span>{escape(name.replace("_", " "))}</span></button>'
        for name in load_feeds())
    return render("reels.html", feed_choices=choices, boot=_page_data({
        "feed_accounts": sorted(feed_accounts()),
        "blocked": sorted(blocked_accounts()),
        "selected_feeds": selected_feeds(),
    }))
