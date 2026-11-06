"""Start the existing ig backend inside the Android application process."""
import json
import os
import secrets
import threading
from http.cookies import SimpleCookie

_lock = threading.Lock()
_server = None
_connection = None


class AppAccess:
    """Only the app's WebView may use the private loopback server."""
    def __init__(self, token):
        self.token = token

    def allow(self, handler):
        expected = f'127.0.0.1:{handler.server.server_port}'
        if handler.headers.get('Host') != expected:
            handler._send(403, b'Forbidden', 'text/plain')
            return False
        origin = handler.headers.get('Origin')
        if origin and origin != 'http://' + expected:
            handler._send(403, b'Forbidden', 'text/plain')
            return False
        try:
            cookies = SimpleCookie(handler.headers.get('Cookie', ''))
            cookie = cookies.get('ig_android')
            valid = cookie is not None and secrets.compare_digest(cookie.value, self.token)
        except Exception:
            valid = False
        if not valid:
            handler._send(403, b'Forbidden', 'text/plain')
        return valid

    def sign_in(self, handler):
        handler._send(403, b'Use the Android app', 'text/plain')


def start(files_dir, cache_dir, version_name='', version_code=''):
    """Called on a background thread; safe to repeat after Activity recreation."""
    global _server, _connection
    with _lock:
        if _server is not None:
            return json.dumps(_connection)
        # Set paths before importing any ig module: modules resolve SQLite paths
        # at import time. App-private storage never touches a desktop profile.
        os.environ['XDG_CONFIG_HOME'] = str(files_dir)
        os.environ['XDG_CACHE_HOME'] = str(cache_dir)
        os.environ['IG_ANDROID'] = '1'
        # Shown under Settings → About.
        os.environ['IG_APP_VERSION'] = str(version_name or '')
        os.environ['IG_APP_BUILD'] = str(version_code or '')
        from ig import config, logs
        from ig.server import make_server
        config.load_feeds()  # Run transactional migrations before showing the UI.
        logs.setup()
        token = secrets.token_urlsafe(32)
        server = make_server('127.0.0.1', 0, access=AppAccess(token))
        thread = threading.Thread(target=server.serve_forever, name='ig-android-http', daemon=True)
        thread.start()
        _server = server
        _connection = {'url': f'http://127.0.0.1:{server.server_port}', 'token': token}
        return json.dumps(_connection)


def link_path(value):
    from ig.links import post_code
    return '/reel/' + post_code(value)


def smoke_test(files_dir, cache_dir):
    """Offline emulator checks. Called only by the debug instrumentation suite."""
    connection = json.loads(start(files_dir, cache_dir))
    import urllib.request
    import urllib.error
    import sqlite3
    from curl_cffi import requests
    from ig.config import CONFIG_DIR, load_feeds, edit_feeds
    checks = []
    with requests.Session(impersonate='chrome'):
        pass
    checks.append('curl-cffi-native-import')
    headers = {'Cookie': 'ig_android=' + connection['token']}

    def request(path, data=None, authorized=True):
        h = dict(headers) if authorized else {}
        if data is not None:
            h['Content-Type'] = 'application/json'
            data = json.dumps(data).encode()
        with urllib.request.urlopen(urllib.request.Request(connection['url'] + path, data=data, headers=h), timeout=10) as response:
            return response.read()

    try:
        request('/feeds', authorized=False)
        raise AssertionError('Unauthenticated access was accepted')
    except urllib.error.HTTPError as error:
        assert error.code == 403
    checks.append('loopback-authentication')
    assert b'android' in request('/')
    assert b'function' in request('/static/reels.js')
    checks.append('packaged-ui-assets')
    previous = load_feeds().get('android_smoke')
    try:
        with edit_feeds() as feeds:
            feeds.pop('android_smoke', None)
        request('/feeds', {'name':'android_smoke', 'accounts':'nasa', 'create':True})
        assert load_feeds().get('android_smoke') == ['nasa']
        with sqlite3.connect(CONFIG_DIR / 'settings.sqlite3') as db:
            assert db.execute("SELECT username FROM feed_accounts WHERE feed = ?", ('android_smoke',)).fetchone()[0] == 'nasa'
    finally:
        with edit_feeds() as feeds:
            if previous is None:
                feeds.pop('android_smoke', None)
            else:
                feeds['android_smoke'] = previous
    checks.append('sqlite-feed-reopen')
    assert json.loads(start(files_dir, cache_dir)) == connection
    checks.append('activity-recreation-reuses-server')
    return json.dumps({'success': True, 'tests': checks})
