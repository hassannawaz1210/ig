"""Portable, versioned copies of local feeds and permanent account blocks."""
import json

from . import config

MAX_BYTES = 5 * 1024 * 1024


class TooLarge(ValueError):
    pass


def _handles(values):
    if not isinstance(values, list):
        raise ValueError('Accounts and blocks must be lists of Instagram handles.')
    if any(not isinstance(value, str) or not config.USER_RE.fullmatch(value) for value in values):
        raise ValueError('The backup contains an invalid Instagram handle.')
    return list(dict.fromkeys(value.lower() for value in values))


def parse(data):
    if len(data) > MAX_BYTES:
        raise TooLarge('Feed backups must be 5 MB or smaller.')
    try:
        document = json.loads(data)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ValueError('Choose a valid ig feed backup JSON file.') from error
    if not isinstance(document, dict) or document.get('format') != 'ig-feeds':
        raise ValueError('This is not an ig feed backup.')
    if type(document.get('version')) is not int or document['version'] != 1:
        raise ValueError('This backup version is not supported.')
    feeds = document.get('feeds')
    if not isinstance(feeds, dict):
        raise ValueError('The backup is missing its feeds.')
    normalized = {}
    for name, accounts in feeds.items():
        if not isinstance(name, str) or not config.FEED_RE.fullmatch(name) or name != name.lower():
            raise ValueError('The backup contains an invalid feed name.')
        normalized[name] = _handles(accounts)
    blocked = _handles(document.get('blocked'))
    return {'format': 'ig-feeds', 'version': 1, 'feeds': normalized, 'blocked': blocked}


def summary(document):
    return {'feeds': len(document['feeds']),
            'accounts': sum(len(accounts) for accounts in document['feeds'].values()),
            'blocked': len(document['blocked']), 'names': list(document['feeds'])}


def export():
    with config.database(readonly=True) as db:
        document = {'format': 'ig-feeds', 'version': 1, 'feeds': config._read_feeds(db),
                    'blocked': [row[0] for row in db.execute('SELECT username FROM blocked_accounts ORDER BY username')]}
    data = json.dumps(document, ensure_ascii=False, separators=(',', ':')).encode() + b'\n'
    if len(data) > MAX_BYTES:
        raise TooLarge('Your feeds exceed the 5 MB backup limit. No backup was created.')
    return data


def merge(document):
    """Merge a validated document in one transaction; keep all existing settings."""
    with config.database() as db:
        feeds = config._read_feeds(db)
        added = 0
        for name, accounts in document['feeds'].items():
            existing = feeds.setdefault(name, [])
            known = set(existing)
            additions = [account for account in accounts if account not in known]
            existing.extend(additions)
            added += len(additions)
        blocks = {row[0] for row in db.execute('SELECT username FROM blocked_accounts')}
        new_blocks = [account for account in document['blocked'] if account not in blocks]
        config._write_feeds(db, feeds)
        db.executemany('INSERT INTO blocked_accounts VALUES (?)', [(account,) for account in new_blocks])
        feed_accounts = list(dict.fromkeys(account for accounts in feeds.values() for account in accounts))
        blocked_accounts = sorted(blocks | set(new_blocks))
    return {'feeds': len(document['feeds']), 'added': added, 'blocked': len(new_blocks),
            'feed_accounts': feed_accounts, 'blocked_accounts': blocked_accounts}
