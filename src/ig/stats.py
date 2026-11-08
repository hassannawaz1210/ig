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
TAG_WINDOW_DAYS = 14      # recent tags compared with everything before
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
        for token, code, owner, viewed, seconds in views:
            recommendations.log_watch(token, code, owner, seconds or 0, viewed=viewed)
        if views:
            log.info('Stats: added %d earlier views to the watch log', len(views))
        _backfilled = True
        return len(views)


def _period_start(days, now):
    return now - days * DAY if days else 0


def compute(days=30, now=None):
    """All stats for the last `days` days (0 = all time)."""
    now = now or time.time()
    start = _period_start(days, now)
    with recommendations.database() as connection:
        rows = connection.execute('SELECT code, owner, viewed, seconds, source, feed, tags FROM watch_log '
                                  'ORDER BY viewed').fetchall()
        feedback = dict(connection.execute('SELECT code, action FROM feedback'))
        feedback_times = connection.execute('SELECT f.action, f.updated FROM feedback f').fetchall()
        saved = connection.execute('SELECT saved_at FROM saved_reels').fetchall()
    period = [row for row in rows if row[2] >= start]
    return {
        'days': days,
        'since': min((row[2] for row in rows), default=None),
        'summary': _summary(period, feedback_times, saved, start),
        'creators': _creators(period, feedback),
        'sources': _sources(period, feedback),
        'weeks': _weeks(rows, now),
        'tags': _tags(rows, now),
        'hours': _hours(period),
    }


def _summary(period, feedback_times, saved, start):
    actions = Counter(action for action, updated in feedback_times if updated >= start)
    return {'reels': len(period),
            'seconds': round(sum(row[3] for row in period)),
            'creators': len({row[1] for row in period}),
            'new_creators': len({row[1] for row in period if row[4] != 'feed'}),
            'likes': actions['like'], 'skips': actions['skip'], 'not_interested': actions['dislike'],
            'saves': sum(1 for (saved_at,) in saved if (saved_at or 0) >= start)}


def _creators(period, feedback):
    seconds, reels, likes, sources = Counter(), Counter(), Counter(), {}
    for code, owner, _, watched, source, _, _ in period:
        seconds[owner] += watched
        reels[owner] += 1
        likes[owner] += feedback.get(code) == 'like'
        sources.setdefault(owner, source)
    return [{'owner': owner, 'seconds': round(total), 'reels': reels[owner], 'likes': likes[owner],
             'source': sources[owner]} for owner, total in seconds.most_common(TOP_CREATORS)]


def _sources(period, feedback):
    groups = defaultdict(list)
    for code, _, _, watched, source, _, _ in period:
        groups[source if source in SOURCE_LABELS else 'other'].append((code, watched))
    result = []
    for source in SOURCE_ORDER:
        items = groups.get(source)
        if not items:
            continue
        actions = Counter(feedback.get(code) for code, _ in items)
        count = len(items)
        result.append({'source': source, 'label': SOURCE_LABELS[source], 'reels': count,
                       'average_seconds': round(sum(w for _, w in items) / count, 1),
                       'likes': actions['like'], 'like_rate': round(actions['like'] / count, 4),
                       'skip_rate': round(actions['skip'] / count, 4)})
    return result


def _week_start(timestamp):
    t = time.localtime(timestamp)
    day = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
    return day - t.tm_wday * DAY


def _weeks(rows, now):
    """Watch time per week for the last WEEKS_SHOWN weeks, split by feed."""
    # From the first week with data (at most WEEKS_SHOWN back), showing at least 4 weeks.
    this_week = _week_start(now)
    first_week = this_week - (WEEKS_SHOWN - 1) * 7 * DAY
    if rows:
        first_week = max(first_week, _week_start(min(row[2] for row in rows)))
    first_week = min(first_week, this_week - 3 * 7 * DAY)
    by_week = defaultdict(Counter)
    totals = Counter()
    for _, _, viewed, watched, _, feed, _ in rows:
        if viewed < first_week:
            continue
        name = feed or '__new__'
        by_week[_week_start(viewed)][name] += watched
        totals[name] += watched
    feeds = [name for name, _ in totals.most_common() if name != '__new__'][:FEEDS_SHOWN]
    series = feeds + (['__other__'] if any(name not in feeds and name != '__new__' for name in totals) else []) \
        + (['__new__'] if totals['__new__'] else [])
    weeks = []
    week = first_week
    while week <= now:
        counts = by_week.get(week, Counter())
        values = {name: 0 for name in series}
        for name, total in counts.items():
            key = name if name in feeds or name == '__new__' else '__other__'
            values[key] += total
        weeks.append({'start': week, 'seconds': {k: round(v) for k, v in values.items()}})
        week += 7 * DAY
    labels = {'__other__': 'Other feeds', '__new__': 'New creators'}
    return {'series': [{'key': name, 'label': labels.get(name, name.replace('_', ' ').title())} for name in series],
            'weeks': weeks}


def _tags(rows, now):
    """Hashtags whose share of your watch time grew or shrank lately."""
    cutoff = now - TAG_WINDOW_DAYS * DAY
    recent, earlier = Counter(), Counter()
    for _, _, viewed, watched, _, _, tags in rows:
        for tag in json.loads(tags or '[]'):
            if tag in recommendations.GENERIC_TAGS:
                continue
            (recent if viewed >= cutoff else earlier)[tag] += watched
    recent_total, earlier_total = sum(recent.values()), sum(earlier.values())
    if not recent_total or not earlier_total:
        return {'rising': [], 'falling': [], 'enough_history': False}
    changes = []
    for tag in set(recent) | set(earlier):
        if recent[tag] + earlier[tag] < MIN_TAG_SECONDS:
            continue
        change = recent[tag] / recent_total - earlier[tag] / earlier_total
        changes.append((change, tag))
    changes.sort()
    as_items = lambda items: [{'tag': tag, 'change': round(change * 100, 1)} for change, tag in items]
    return {'rising': as_items([c for c in reversed(changes) if c[0] > 0][:RISING_TAGS]),
            'falling': as_items([c for c in changes if c[0] < 0][:RISING_TAGS]),
            'enough_history': True}


def _hours(period):
    seconds = [0.0] * 24
    for _, _, viewed, watched, _, _, _ in period:
        seconds[time.localtime(viewed).tm_hour] += watched
    return [round(s) for s in seconds]
