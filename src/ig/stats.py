"""Numbers for the stats page, from the permanent watch log.

Everything is computed from what this device recorded: the watch log (one row
per reel watched, with seconds, where the reel came from and its creator's
feed), likes / "not interested" / quick skips, and saved reels.
"""
import json
import logging
import threading
import time
from collections import Counter, defaultdict

from . import config, history, recommendations

log = logging.getLogger(__name__)

DAY = 86400
WEEKS_SHOWN = 12          # taste over time
FEEDS_SHOWN = 6           # + "New creators" = 7 validated colours; the rest is grey "Other feeds"
TOP_CREATORS = 15
RISING_TAGS = 6
TAG_WINDOW_DAYS = 14      # recent tags ...
TAG_BASELINE_DAYS = 90    # ... compared with the 90 days before them
MIN_TAG_SECONDS = 60      # ignore tags with too little watching to compare
# Sources in a fixed order, so colours and rows never shift between periods.
SOURCE_ORDER = ['feed', 'suggested', 'popular_topic', 'coauthor', 'credit', 'mention',
                'reply_mention', 'audio_creator', 'hashtag', 'commenter', 'liked_creator', 'other']
SOURCE_LABELS = {'feed': 'Your feeds', 'suggested': 'Instagram suggestions', 'popular_topic': 'Popular topics',
                 'coauthor': 'Collaborators', 'credit': 'Credited creators', 'mention': 'Mentions',
                 'reply_mention': 'Mentions in replies', 'audio_creator': 'Audio creators', 'hashtag': 'Hashtags',
                 'commenter': 'Commenters', 'liked_creator': 'Creators you liked', 'other': 'Other'}

_backfill_lock = threading.Lock()
_backfilled = False


def backfill():
    """Copy views from the recent viewing history that the watch log lacks
    (views from before the log existed). Safe to repeat; runs once per start."""
    global _backfilled
    with _backfill_lock:
        if _backfilled:
            return 0
        with recommendations.database() as connection:
            logged = {row[0] for row in connection.execute('SELECT token FROM watch_log')}
        with history.database() as db:
            views = [row for row in db.execute('SELECT token, code, owner, viewed, seconds FROM viewings')
                     if row[0] not in logged]
        recommendations.log_watches([(token, code, owner, seconds or 0, viewed)
                                     for token, code, owner, viewed, seconds in views])
        if views:
            log.info('Stats: added %d earlier views to the watch log', len(views))
        _backfilled = True
        return len(views)


def _period_start(days, now):
    """Where a period begins: 'today' starts at local midnight, a number of
    days counts back from now, and 0 means all time."""
    if days == 'today':
        t = time.localtime(now)
        return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
    return now - days * DAY if days else 0


CACHE_SECONDS = 30
_cache = {}            # (days, part) -> (version, computed_at, result)
_cache_lock = threading.Lock()
PARTS = ('overview', 'weeks', 'tags')


def _version(connection):
    """Changes whenever a reel is logged, a reaction is recorded, or a reel is
    saved (all O(1) or tiny lookups), so a cached result is never stale for long."""
    return (connection.execute('SELECT MAX(rowid) FROM watch_log').fetchone()[0],
            connection.execute('SELECT MAX(updated), COUNT(*) FROM feedback').fetchone(),
            connection.execute('SELECT MAX(saved_at), COUNT(*) FROM saved_reels').fetchone())


def _compute_part(connection, part, days, now):
    if part == 'weeks':          # the last WEEKS_SHOWN weeks, whatever the period
        return {'weeks': _weeks(connection, now)}
    if part == 'tags':           # the last TAG_WINDOW_DAYS against the time before
        return {'tags': _tags(connection, now)}
    start = _period_start(days, now)
    # One grouped scan of the period (viewed index) supplies the overview,
    # creators, sources and hours.
    groups = connection.execute(
        "SELECT w.owner, w.source, CAST(strftime('%H', w.viewed, 'unixepoch', 'localtime') AS INTEGER), "
        "COUNT(*), SUM(w.seconds), SUM(f.action = 'like'), SUM(f.action = 'skip') "
        "FROM watch_log w LEFT JOIN feedback f ON f.code = w.code WHERE w.viewed >= ? GROUP BY 1, 2, 3",
        (start,)).fetchall()
    return {
        'days': days,
        'since': connection.execute('SELECT MIN(viewed) FROM watch_log').fetchone()[0],
        'summary': _summary(connection, start, groups),
        'creators': _creators(groups),
        'sources': _sources(groups),
        'hours': _hours(groups),
    }


def compute(days=30, now=None, part=None):
    """Stats for the last `days` days (0 = all time, 'today' = since midnight).

    `part` is one of PARTS, so the page can show each section as soon as its
    own data is ready: 'overview' (tiles, creators, sources, hours; depends
    on the period), 'weeks' and 'tags' (their own fixed windows). None
    returns everything. Results are reused until something new is recorded
    (at most CACHE_SECONDS), so switching periods back and forth is instant."""
    if part is None:
        result = {}
        for name in PARTS:
            result.update(compute(days, now, name))
        return result
    if part not in PARTS:
        raise ValueError(f'Unknown stats part: {part}')
    fixed_now = now is not None
    now = now or time.time()
    key = (days if part == 'overview' else None, part)
    with recommendations.database() as connection:
        version = _version(connection)
        with _cache_lock:
            cached = _cache.get(key)
        if not fixed_now and cached and cached[0] == version and now - cached[1] < CACHE_SECONDS:
            return cached[2]
        result = _compute_part(connection, part, days, now)
    if not fixed_now:
        with _cache_lock:
            _cache[key] = (version, now, result)
    return result


def _summary(connection, start, groups):
    owners, new_owners = set(), set()
    reels = seconds = 0
    for owner, source, _, count, total, _, _ in groups:
        reels += count
        seconds += total or 0
        owners.add(owner)
        if source != 'feed':
            new_owners.add(owner)
    actions = dict(connection.execute('SELECT action, COUNT(*) FROM feedback WHERE updated >= ? GROUP BY action',
                                      (start,)))
    saves = connection.execute('SELECT COUNT(*) FROM saved_reels WHERE saved_at >= ?', (start,)).fetchone()[0]
    return {'reels': reels, 'seconds': round(seconds), 'creators': len(owners), 'new_creators': len(new_owners),
            'likes': actions.get('like', 0), 'skips': actions.get('skip', 0),
            'not_interested': actions.get('dislike', 0), 'saves': saves}


def _creators(groups):
    seconds, reels, likes, in_feed = Counter(), Counter(), Counter(), set()
    for owner, source, _, count, total, liked, _ in groups:
        seconds[owner] += total or 0
        reels[owner] += count
        likes[owner] += liked or 0
        if source == 'feed':
            in_feed.add(owner)
    return [{'owner': owner, 'seconds': round(total), 'reels': reels[owner], 'likes': likes[owner],
             'source': 'feed' if owner in in_feed else 'new'} for owner, total in seconds.most_common(TOP_CREATORS)]


def _sources(groups):
    found = {}
    for _, source, _, count, total, liked, skipped in groups:
        key = source if source in SOURCE_LABELS else 'other'
        entry = found.setdefault(key, [0, 0.0, 0, 0])
        entry[0] += count; entry[1] += total or 0; entry[2] += liked or 0; entry[3] += skipped or 0
    return [{'source': key, 'label': SOURCE_LABELS[key], 'reels': reels,
             'average_seconds': round(total / reels, 1), 'likes': likes,
             'like_rate': round(likes / reels, 4), 'skip_rate': round(skips / reels, 4)}
            for key in SOURCE_ORDER if key in found
            for reels, total, likes, skips in [found[key]]]


def _week_start(timestamp):
    t = time.localtime(timestamp)
    day = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
    return day - t.tm_wday * DAY


def _weeks(connection, now):
    """Watch time per week (Monday start, local time) for up to WEEKS_SHOWN weeks, by feed."""
    this_week = _week_start(now)
    first_week = this_week - (WEEKS_SHOWN - 1) * 7 * DAY
    oldest = connection.execute('SELECT MIN(viewed) FROM watch_log').fetchone()[0]
    if oldest is not None:
        first_week = max(first_week, _week_start(oldest))
    first_week = min(first_week, this_week - 3 * 7 * DAY)   # at least 4 weeks
    by_week = defaultdict(Counter)
    totals = Counter()
    for week, feed, total in connection.execute(
            "SELECT date(viewed, 'unixepoch', 'localtime', 'weekday 0', '-6 days'), COALESCE(feed, '__new__'), "
            "SUM(seconds) FROM watch_log WHERE viewed >= ? GROUP BY 1, 2", (first_week,)):
        start = time.mktime(time.strptime(week, '%Y-%m-%d'))
        by_week[start][feed] += total
        totals[feed] += total
    feeds = [name for name, _ in totals.most_common() if name != '__new__'][:FEEDS_SHOWN]
    others = any(name not in feeds and name != '__new__' for name in totals)
    series = feeds + (['__other__'] if others else []) + (['__new__'] if totals['__new__'] else [])
    weeks, week = [], first_week
    while week <= now:
        values = dict.fromkeys(series, 0)
        for name, total in by_week.get(week, {}).items():
            values[name if name in feeds or name == '__new__' else '__other__'] += total
        weeks.append({'start': week, 'seconds': {k: round(v) for k, v in values.items()}})
        week = _week_start(week + 8 * DAY)   # next Monday, safe across clock changes
    labels = {'__other__': 'Other feeds', '__new__': 'New creators'}
    return {'series': [{'key': name, 'label': labels.get(name, name.replace('_', ' ').title())} for name in series],
            'weeks': weeks}


def _tag_seconds(connection, start, end):
    """Watch time per hashtag between two times (SQLite's json_each)."""
    try:
        return Counter(dict(connection.execute(
            'SELECT j.value, SUM(w.seconds) FROM watch_log w, json_each(w.tags) j '
            'WHERE w.viewed >= ? AND w.viewed < ? GROUP BY j.value', (start, end))))
    except Exception:   # an SQLite without JSON support: same result, in Python
        log.warning('SQLite lacks json_each; adding up hashtags in Python', exc_info=True)
        totals = Counter()
        for tags, seconds in connection.execute('SELECT tags, seconds FROM watch_log WHERE viewed >= ? AND viewed < ?',
                                                (start, end)):
            for tag in json.loads(tags or '[]'):
                totals[tag] += seconds
        return totals


def _tags(connection, now):
    """Hashtags whose share of your watch time grew or shrank lately: the last
    TAG_WINDOW_DAYS against the TAG_BASELINE_DAYS before them."""
    cutoff = now - TAG_WINDOW_DAYS * DAY
    recent = _tag_seconds(connection, cutoff, now + DAY)
    earlier = _tag_seconds(connection, cutoff - TAG_BASELINE_DAYS * DAY, cutoff)
    for counts in (recent, earlier):
        for tag in [tag for tag in counts if tag in recommendations.GENERIC_TAGS]:
            del counts[tag]
    recent_total, earlier_total = sum(recent.values()), sum(earlier.values())
    if not recent_total or not earlier_total:
        return {'rising': [], 'falling': [], 'enough_history': False}
    changes = sorted((recent[tag] / recent_total - earlier[tag] / earlier_total, tag)
                     for tag in set(recent) | set(earlier) if recent[tag] + earlier[tag] >= MIN_TAG_SECONDS)
    as_items = lambda items: [{'tag': tag, 'change': round(change * 100, 1)} for change, tag in items]
    return {'rising': as_items([c for c in reversed(changes) if c[0] > 0][:RISING_TAGS]),
            'falling': as_items([c for c in changes if c[0] < 0][:RISING_TAGS]),
            'enough_history': True}


def _hours(groups):
    hours = [0.0] * 24
    for _, _, hour, _, total, _, _ in groups:
        hours[hour] += total or 0
    return [round(value) for value in hours]
