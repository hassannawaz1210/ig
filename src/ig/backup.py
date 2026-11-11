"""Portable feeds, saved reel metadata, permanent blocks, and app settings."""
import json
import math
import re

from . import config, recommendations

MAX_BYTES = 5 * 1024 * 1024
CODE_RE = re.compile(r'^[A-Za-z0-9_-]{5,30}$')
SAVED_FIELDS = ('shortcode', 'owner', 'caption', 'taken_at', 'like_count', 'comment_count', 'saved_at')
SETTING_KEYS = {'sound', 'auto_scroll', 'scroll_animation', 'selected_feeds'}


class TooLarge(ValueError):
    pass


def _handles(values):
    if not isinstance(values, list):
        raise ValueError('Accounts and blocks must be lists of Instagram handles.')
    if any(not isinstance(value, str) or not config.USER_RE.fullmatch(value) for value in values):
        raise ValueError('The backup contains an invalid Instagram handle.')
    return list(dict.fromkeys(value.lower() for value in values))


def _saved_reels(values):
    if not isinstance(values, list):
        raise ValueError('Saved reels must be a list.')
    reels = {}
    for value in values:
        if not isinstance(value, dict) or set(value) - set(SAVED_FIELDS):
            raise ValueError('The backup contains unsupported saved reel fields.')
        code, owner = value.get('shortcode'), value.get('owner', '')
        if not isinstance(code, str) or not CODE_RE.fullmatch(code):
            raise ValueError('The backup contains an invalid saved reel shortcode.')
        if not isinstance(owner, str) or (owner and not config.USER_RE.fullmatch(owner)):
            raise ValueError('The backup contains an invalid saved reel owner.')
        caption = value.get('caption')
        if caption is not None and not isinstance(caption, str):
            raise ValueError('Saved reel captions must be text.')
        for field in ('taken_at', 'like_count', 'comment_count'):
            number = value.get(field)
            if number is not None and (type(number) is not int or not 0 <= number < 2**63):
                raise ValueError('Saved reel counts and dates must be non-negative integers.')
        saved_at = value.get('saved_at')
        if type(saved_at) not in (int, float) or not 0 <= saved_at < 2**63 or not math.isfinite(saved_at):
            raise ValueError('Saved reels need a valid saved_at timestamp.')
        reels.setdefault(code, {**{field: value.get(field) for field in SAVED_FIELDS},
                                'owner': owner.lower()})
    return list(reels.values())


def _settings(value):
    if not isinstance(value, dict) or set(value) - SETTING_KEYS:
        raise ValueError('The backup contains unsupported app settings.')
    for key in ('sound', 'auto_scroll'):
        if key in value and type(value[key]) is not bool:
            raise ValueError('Sound and auto-scroll must be true or false.')
    if 'scroll_animation' in value and value['scroll_animation'] not in ('smooth', 'snappy'):
        raise ValueError('Choose smooth or snappy scrolling.')
    selection = value.get('selected_feeds')
    if selection is not None:
        if not isinstance(selection, list) or any(not isinstance(name, str) or
                not config.FEED_RE.fullmatch(name) for name in selection):
            raise ValueError('Selected feeds must be a list of feed names or null.')
        value = {**value, 'selected_feeds': list(dict.fromkeys(selection))}
    return dict(value)


def parse(data):
    if len(data) > MAX_BYTES:
        raise TooLarge('Backups must be 5 MB or smaller.')
    try:
        document = json.loads(data)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ValueError('Choose a valid ig backup JSON file.') from error
    if not isinstance(document, dict) or document.get('format') != 'ig-feeds':
        raise ValueError('This is not an ig backup.')
    if type(document.get('version')) is not int or document['version'] not in (1, 2):
        raise ValueError('This backup version is not supported.')
    feeds = document.get('feeds')
    if not isinstance(feeds, dict):
        raise ValueError('The backup is missing its feeds.')
    normalized = {}
    for name, accounts in feeds.items():
        if not isinstance(name, str) or not config.FEED_RE.fullmatch(name) or name != name.lower():
            raise ValueError('The backup contains an invalid feed name.')
        normalized[name] = _handles(accounts)
    result = {'format': 'ig-feeds', 'version': document['version'],
              'feeds': normalized, 'blocked': _handles(document.get('blocked'))}
    if document['version'] == 2:
        result['saved_reels'] = _saved_reels(document.get('saved_reels', []))
        result['settings'] = _settings(document.get('settings', {}))
    # Optional; older versions of ig ignore this field when importing.
    result['feed_topics'] = _feed_topics(document.get('feed_topics', {}), normalized)
    return result


def _feed_topics(value, feeds):
    """Each feed's extra discovery topics; only for feeds in this backup."""
    if not isinstance(value, dict):
        raise ValueError('The backup contains invalid feed topics.')
    topics = {}
    for name, words in value.items():
        if name not in feeds:
            raise ValueError('The backup has topics for a feed it does not contain.')
        cleaned = config.clean_topic_words(words)
        if cleaned:
            topics[name] = cleaned
    return topics


def summary(document):
    return {'feeds': len(document['feeds']),
            'accounts': sum(len(accounts) for accounts in document['feeds'].values()),
            'blocked': len(document['blocked']), 'names': list(document['feeds']),
            'saved_reels': len(document.get('saved_reels', [])),
            'settings': list(document.get('settings', {}))}


def _attach_saved_reels(db):
    db.execute('ATTACH DATABASE ? AS saved_data',
               (str(config.CONFIG_DIR / 'recommendations.sqlite3'),))


def select_feeds(document, names=None):
    """Keep chosen feeds and their topics without changing global data."""
    if names is None:
        return document
    if not isinstance(names, list) or any(not isinstance(name, str) or name not in document['feeds'] for name in names):
        raise ValueError('Choose feeds present in this backup.')
    chosen = set(names)
    result = {**document, 'feeds': {name: accounts for name, accounts in document['feeds'].items() if name in chosen},
              'feed_topics': {name: words for name, words in document.get('feed_topics', {}).items() if name in chosen}}
    if chosen != set(document['feeds']) and 'settings' in document:
        result['settings'] = {key: value for key, value in document['settings'].items() if key != 'selected_feeds'}
    return result


def export(names=None):
    # Complete schema upgrades before attaching; the snapshot itself uses one
    # connection and reads only explicitly portable fields from both databases.
    with recommendations.database():
        pass
    with config.database(readonly=True) as db:
        _attach_saved_reels(db)
        feeds = config._read_feeds(db)
        preferences = dict(db.execute('SELECT key, value FROM text_preferences'))
        selection = json.loads(preferences['selected_feeds'])
        if selection is not None:
            selection = [name for name in selection if name in feeds]
        settings = {**config._playback_preferences(db),
                    'scroll_animation': preferences.get('scroll_animation', 'smooth'),
                    'selected_feeds': selection}
        saved = [dict(zip(SAVED_FIELDS, row)) for row in db.execute(
            'SELECT code, owner, caption, taken_at, like_count, comment_count, saved_at '
            'FROM saved_data.saved_reels ORDER BY saved_at DESC, code')]
        topics = {}
        for name, word in db.execute('SELECT feed, word FROM feed_topics ORDER BY feed, position'):
            if name in feeds:
                topics.setdefault(name, []).append(word)
        document = {'format': 'ig-feeds', 'version': 2, 'feeds': feeds,
                    'blocked': [row[0] for row in db.execute('SELECT username FROM blocked_accounts ORDER BY username')],
                    'saved_reels': saved, 'settings': settings, 'feed_topics': topics}
    document = select_feeds(document, names)
    data = json.dumps(document, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode() + b'\n'
    if len(data) > MAX_BYTES:
        raise TooLarge('Your data exceeds the 5 MB backup limit. No backup was created.')
    parse(data)  # Never offer a file this version of ig cannot import.
    return data


def _merge_saved_reels(db, saved):
    before = db.total_changes
    db.executemany('INSERT OR IGNORE INTO saved_data.saved_reels '
        '(code, owner, caption, url, taken_at, like_count, comment_count, image, saved_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)',
        [(reel['shortcode'], reel['owner'], reel['caption'],
          'https://www.instagram.com/reel/' + reel['shortcode'] + '/',
          reel['taken_at'], reel['like_count'], reel['comment_count'], reel['saved_at']) for reel in saved])
    return db.total_changes - before


def _merge_settings(db, settings):
    db.executemany('INSERT INTO text_preferences(key, value) VALUES (?, ?) '
        'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
        [(key, value if key == 'scroll_animation' else json.dumps(value)) for key, value in settings.items()])


def merge(document):
    """Merge a validated backup in one transaction; existing saved records win."""
    saved = document.get('saved_reels', []) if document['version'] == 2 else []
    settings = document.get('settings', {}) if document['version'] == 2 else {}
    if saved:
        with recommendations.database():
            pass
    with config.database() as db:
        if saved:
            _attach_saved_reels(db)
        feeds = config._read_feeds(db)
        added = 0
        for name, accounts in document['feeds'].items():
            existing = feeds.setdefault(name, [])
            known = set(existing)
            additions = [account for account in accounts if account not in known]
            existing.extend(additions)
            added += len(additions)
        selection = settings.get('selected_feeds')
        if selection is not None and any(name not in feeds for name in selection):
            raise ValueError('The selected feeds are missing from this backup and your profile.')
        blocks = {row[0] for row in db.execute('SELECT username FROM blocked_accounts')}
        new_blocks = [account for account in document['blocked'] if account not in blocks]
        config._write_feeds(db, feeds)
        for name, words in document.get('feed_topics', {}).items():
            # Add topics the profile doesn't have yet, after its own.
            existing = [row[0] for row in db.execute('SELECT word FROM feed_topics WHERE feed=? ORDER BY position', (name,))]
            merged = config.clean_topic_words(list(dict.fromkeys(existing + words))[:config.MAX_TOPIC_WORDS])
            db.execute('DELETE FROM feed_topics WHERE feed=?', (name,))
            db.executemany('INSERT INTO feed_topics(feed, word, position) VALUES (?, ?, ?)',
                           [(name, word, i) for i, word in enumerate(merged)])
        db.executemany('INSERT INTO blocked_accounts VALUES (?)', [(account,) for account in new_blocks])
        saved_added = _merge_saved_reels(db, saved) if saved else 0
        _merge_settings(db, settings)
        feed_accounts = list(dict.fromkeys(account for accounts in feeds.values() for account in accounts))
        blocked_accounts = sorted(blocks | set(new_blocks))
    return {'feeds': len(document['feeds']), 'added': added, 'blocked': len(new_blocks),
            'saved_reels_added': saved_added, 'settings_applied': list(settings), 'settings_values': settings,
            'saved_codes': [reel['shortcode'] for reel in saved],
            'feed_accounts': feed_accounts, 'blocked_accounts': blocked_accounts}
