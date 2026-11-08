"""Render the profile and reels pages from the templates in ig/static.

Templates use {{name}} for values (escaped by the caller) and {{icon:name}}
for inline SVG icons. CSS and JavaScript are plain files served from /static."""
import json
import re
from functools import lru_cache
from html import escape
from pathlib import Path
from urllib.parse import quote

from .icons import icon, icons_js
from . import config
from .config import blocked_accounts, load_feeds, selected_feeds
from .feeds import feed_accounts


MENTION_RE = re.compile(r'(^|[^\w.@])@([A-Za-z0-9._]{1,30})')


def link_mentions(text: str) -> str:
    """Escaped text with each @username linked to that profile in ig (same
    rules as linkifyMentions in common.js)."""
    def link(match):
        before, name = match.groups()
        username = name.rstrip('.')
        if not username:
            return match.group(0)
        return (f'{before}<a class="mention" href="/u/{escape(username.lower(), quote=True)}">'
                f'@{username}</a>{name[len(username):]}')
    return MENTION_RE.sub(link, escape(text or ''))

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


def _sound_summary(sound: dict) -> str:
    """Header for a sound page: track, artist, reel count, like Instagram's audio page."""
    title = sound.get('title') or 'Sound'
    artist, _, track = title.partition(' | ')
    if not track:
        artist, track = '', title
    if track.lower() == 'original audio' and config.USER_RE.fullmatch(artist):
        # An original sound's "artist" is the creator who first posted it.
        artist_html = f'<p>Original audio by <a href="/u/{escape(artist.lower(), quote=True)}">@{escape(artist)}</a></p>'
        track = 'Original audio'
    else:
        artist_html = f'<p>{escape(artist)}</p>' if artist else ''
    count = sound.get('total')
    audio_id = escape(str(sound.get('id') or ''), quote=True)
    return ('<section class="profile-summary sound-summary" aria-label="Sound">'
            f'<div class="profile-avatar sound-art" aria-hidden="true">{icon("music")}</div>'
            '<div class="profile-bio">'
            f'<strong>{escape(track)}</strong>{artist_html}'
            + (f'<p class="sound-count">{escape(str(count))} on Instagram</p>' if count else '')
            + f'<div class="profile-links"><a href="https://www.instagram.com/reels/audio/{audio_id}/" '
              'target="_blank" rel="noopener">Open on Instagram</a></div></div></section>')


def _topic_summary(topic: dict) -> str:
    """Header for a topic page: its name, post count and a link to Instagram's page."""
    name = topic.get('name') or 'Topic'
    slug = quote(str(topic.get('slug') or ''), safe='-')
    count = topic.get('count_label')
    return ('<section class="profile-summary sound-summary topic-summary" aria-label="Topic">'
            f'<div class="profile-avatar sound-art" aria-hidden="true">{icon("search")}</div>'
            '<div class="profile-bio">'
            f'<strong>{escape(name)}</strong>'
            + (f'<p class="sound-count">{escape(count)} on Instagram</p>' if count else '')
            + f'<div class="profile-links"><a href="https://www.instagram.com/popular/{escape(slug, quote=True)}/" '
              'target="_blank" rel="noopener">Open on Instagram</a></div></div></section>')


def viewer_html(title: str, subtitle: str, posts: list[dict],
                username: str = "", paged: bool = True, empty_message: str = "", profile: dict | None = None,
                sound: dict | None = None, topic: dict | None = None) -> str:
    """The post grid page. paged=False shows every post at once, without a
    pagination indicator; empty_message is shown when there are no posts."""
    add_button = ""
    if username:
        saved = username in feed_accounts()
        add_button = (f'<button id="addacc" data-state="{"in" if saved else "out"}" '
                      f'onclick="toggleAcc({escape(json.dumps(username), quote=True)}, this)">'
                      + (icon("check") + " in feed" if saved else icon("plus") + " add to feed")
                      + "</button>")
    profile_header = ''
    if username:
        profile_header = _profile_summary(username, profile or {}, add_button)
        add_button = ''
    elif sound:
        profile_header = _sound_summary(sound)
    elif topic:
        profile_header = _topic_summary(topic)
    return render("profile.html", title=escape(title), subtitle=escape(subtitle), profile_header=profile_header,
                  add_button=add_button,
                  more_button='<div id="more" role="status" aria-live="polite"></div>' if paged else "",
                  boot=_page_data({"posts": posts, "username": username,
                                   "empty_message": empty_message, "android": config.ANDROID,
                                   **config.playback_preferences(include_missing=True)}))


def compact_count(value) -> str | None:
    """Counts the way Instagram shows them: 640, 4,154, 12.5K, 681M. None if unknown."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    if value < 10_000:
        return f'{value:,}'  # Instagram writes 4,154 in full.
    units = ((1_000_000_000, 'B'), (1_000_000, 'M'), (1_000, 'K'))
    for index, (size, suffix) in enumerate(units):
        if value >= size:
            if round(value / size, 1) >= 1000 and index:  # 999,999 is 1M, not 1000K.
                size, suffix = units[index - 1]
            return f'{value / size:.1f}'.removesuffix('.0') + suffix


def _web_link(url) -> str:
    """url when it is an http(s) link, else '', so javascript: never reaches an href."""
    if not isinstance(url, str) or not url.startswith(('https://', 'http://')):
        return ''
    return url


def _profile_link(url: str) -> str:
    # Shown the way Instagram does: hrbl.me/CR7Pro2col, without scheme or www.
    label = url.split('://', 1)[1].removeprefix('www.').rstrip('/')
    return (f'<a class="profile-link" href="{escape(url, quote=True)}" target="_blank" '
            f'rel="noopener noreferrer">{escape(label)}</a>')


def _profile_summary(username: str, profile: dict, add_button: str) -> str:
    """Picture, counts, name, bio and links, as instagram.com shows them to a
    logged-out visitor. Every Instagram value is escaped; unknown values are left out."""
    avatar = _web_link(profile.get('avatar'))
    avatar_html = (f'<img src="{escape(avatar, quote=True)}" alt="" referrerpolicy="no-referrer" '
                   'onerror="this.hidden=true">') if avatar else ''  # The initial shows through.
    counts = ''
    for key, one, many in (('post_count', 'post', 'posts'), ('followers', 'follower', 'followers'),
                           ('following', 'following', 'following')):
        shown = compact_count(profile.get(key))
        if shown is not None:
            counts += f'<div><dt>{one if profile.get(key) == 1 else many}</dt><dd>{shown}</dd></div>'
    links = [url for url in (_web_link(link.get('url')) for link in profile.get('links') or []
                             if isinstance(link, dict)) if url]
    if not links and _web_link(profile.get('external_url')):
        links = [profile['external_url']]  # Profiles cached before links were stored.
    links_html = ''
    if links:
        more = (f'<details><summary>and {len(links) - 1} more</summary>'
                + ''.join(_profile_link(url) for url in links[1:]) + '</details>') if len(links) > 1 else ''
        links_html = f'<div class="profile-links">{_profile_link(links[0])}{more}</div>'
    biography = profile.get('biography') or ''
    return ('<section class="profile-summary" aria-label="Profile">'
            f'<div class="profile-avatar"><span>{escape(username[:1].upper())}</span>{avatar_html}</div>'
            + (f'<dl class="profile-counts">{counts}</dl>' if counts else '')
            + '<div class="profile-bio">'
            f'<strong>{escape(profile.get("full_name") or username)}</strong>'
            + ('<span class="verified" role="img" aria-label="Verified">' + icon('check') + '</span>'
               if profile.get('verified') else '')
            + (f'<p>{link_mentions(biography)}</p>' if biography else '')
            + links_html + '</div>'
            f'<div class="profile-actions">{add_button}</div></section>')


def reels_html(code: str | None = None) -> str:
    """The reels page shell. The page fetches its reels after it loads."""
    check = icon("check")
    choices = "".join(
        f'<div class="feed-row"><button type="button" class="feed-choice" data-feed="{escape(name, quote=True)}" '
        f'aria-pressed="false">{check}<span>{escape(name.replace("_", " "))}</span></button>'
        f'<button type="button" class="feed-delete" data-feed="{escape(name, quote=True)}" '
        f'aria-label="Delete {escape(name, quote=True)} feed" title="Delete feed">{icon("trash")}</button></div>'
        for name in load_feeds())
    return render("reels.html", feed_choices=choices, boot=_page_data({
        "post_code": code,
        "android": config.ANDROID,
        "scroll_animation": config.scroll_animation(),
        **config.playback_preferences(include_missing=True),
        "feeds": list(load_feeds()),
        "feed_accounts": sorted(feed_accounts()),
        "blocked": sorted(blocked_accounts()),
        "selected_feeds": selected_feeds(),
    }))


def stats_html() -> str:
    """Settings → Your stats. The page loads its numbers itself."""
    return render('stats.html', boot=_page_data({'android': config.ANDROID}))


def trending_html() -> str:
    """Trending (bottom bar): Instagram's Explore lists; the page loads them itself."""
    placeholder = '<div class="trend-card placeholder" aria-hidden="true"><i></i><i></i><i></i></div>'
    return render('trending.html', placeholders=placeholder * 8, boot=_page_data({'android': config.ANDROID}))


def similar_html(username: str) -> str:
    """Every account Instagram suggests for a profile; the page loads them itself."""
    placeholder = '<div class="similar-card placeholder" aria-hidden="true"><i></i><i></i><i></i><i></i></div>'
    return render('similar.html', title='Suggested accounts', username=escape(username),
                  back=escape('/u/' + quote(username), quote=True), placeholders=placeholder * 8,
                  boot=_page_data({'android': config.ANDROID, 'username': username}))


def feeds_html() -> str:
    """Settings → Feeds: every feed and its accounts. The page loads them itself."""
    return render('feeds.html', boot=_page_data({'android': config.ANDROID}))


def settings_html() -> str:
    return render('settings.html', boot=_page_data({
        'android': config.ANDROID,
        'scroll_animation': config.scroll_animation(),
        **config.app_version(),
        'repository': config.REPOSITORY_URL,
    }))


def status_html(title: str, message: str, *, retry: bool = True) -> str:
    actions = ('<a class="primary" id="retry-page" href="">Try again</a>' if retry else
               '<a class="primary" href="https://github.com/hassannawaz1210/ig/releases/tag/android-updates">Open downloads</a>')
    actions += '<a href="/">Back to reels</a>'
    return render("status.html", title=escape(title), message=escape(message), actions=actions)
