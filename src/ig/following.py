"""Read Instagram following exports locally, without extracting archives to disk."""
import io
import json
import re
import zipfile
import zlib
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

MAX_UPLOAD = 25 * 1024 * 1024
MAX_CONTENT = 10 * 1024 * 1024
MAX_ACCOUNTS = 7500
FOLLOWING_FILE = re.compile(r'following(?:_\d+)?\.(json|html?)$', re.I)
HANDLE = re.compile(r'[a-zA-Z0-9._]{1,30}')
RESERVED = {'p', 'reel', 'reels', 'explore', 'accounts', 'direct', 'stories'}


class TooLarge(ValueError):
    pass


def username(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if '://' in value:
        try:
            url = urlsplit(value)
            if url.scheme not in ('https', 'http') or url.hostname not in ('instagram.com', 'www.instagram.com'):
                return None
            value = unquote(url.path).strip('/')
        except ValueError:
            return None
        if value.startswith('_u/'):
            value = value[3:]
    value = value.lstrip('@').lower()
    return value if HANDLE.fullmatch(value) and value not in RESERVED else None


class FollowingHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.accounts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            href = dict(attrs).get('href', '')
            if href.startswith(('https://', 'http://')):
                account = username(href)
                if account:
                    self.accounts.append(account)


def _parse_file(data, filename):
    if len(data) > MAX_CONTENT:
        raise TooLarge('Following export is too large (10 MB maximum).')
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError as error:
        raise ValueError('Choose an Instagram following JSON, HTML, or ZIP export.') from error
    if filename.lower().endswith(('.html', '.htm')):
        if not FOLLOWING_FILE.fullmatch(filename):
            raise ValueError('Choose following.html, not a followers list or another page.')
        parser = FollowingHTML()
        parser.feed(text)
        return parser.accounts
    try:
        document = json.loads(text)
    except (ValueError, RecursionError) as error:
        raise ValueError('Could not read this following export. Choose following.json, following.html, or its ZIP.') from error
    if not isinstance(document, dict) or not isinstance(document.get('relationships_following'), list):
        raise ValueError('This is not an Instagram following export. Choose following.json, not followers.json.')
    accounts = []
    for row in document['relationships_following']:
        if not isinstance(row, dict):
            continue
        entries = row.get('string_list_data')
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            account = username(entry.get('value')) or username(entry.get('href'))
            if account:
                accounts.append(account)
    return accounts


def parse_export(data, filename=''):
    """Return unique handles only; never read followers or unrelated ZIP files."""
    if len(data) > MAX_UPLOAD:
        raise TooLarge('Upload is too large (25 MB maximum). Extract following.json or following.html from the ZIP, or export only followers and following.')
    accounts = []
    if data.startswith(b'PK') or filename.lower().endswith('.zip'):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if len(archive.infolist()) > 10000:
                    raise TooLarge('This ZIP contains too many files. Select following.json instead.')
                files = [item for item in archive.infolist() if not item.is_dir()
                         and FOLLOWING_FILE.fullmatch(PurePosixPath(item.filename).name)]
                if not files:
                    raise ValueError('No following.json or following.html found in this ZIP.')
                total = 0
                for item in files:
                    if item.flag_bits & 1:
                        raise ValueError('Password-protected ZIP files cannot be imported.')
                    total += item.file_size
                    if total > MAX_CONTENT:
                        raise TooLarge('Following export is too large (10 MB maximum).')
                    with archive.open(item) as stream:
                        content = stream.read(MAX_CONTENT + 1)
                    accounts.extend(_parse_file(content, PurePosixPath(item.filename).name))
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError, zlib.error) as error:
            raise ValueError('Could not read this ZIP. Select following.json or following.html instead.') from error
    else:
        accounts = _parse_file(data, PurePosixPath(filename).name)
    accounts = list(dict.fromkeys(accounts))
    if not accounts:
        raise ValueError('No followed accounts found in this export.')
    if len(accounts) > MAX_ACCOUNTS:
        raise TooLarge('Import at most 7,500 accounts at once.')
    return accounts
