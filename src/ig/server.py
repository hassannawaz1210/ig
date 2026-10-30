from . import recommendations
from .media import fetch_video
import json
import shutil
import tempfile
from curl_cffi import requests as cr
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from .config import CACHE, SERVER_JSON, USER_RE, FEED_RE, load_feeds, save_feeds, accounts_from_feed, prefer_memes, set_prefer_memes, blocked_accounts, set_blocked
from .feeds import MIX, feed_pool, pool_more, discover_into, feed_accounts
from .instagram import STATE, init_state, more_posts, media_info
from .recognition import recognize_song
from .views import viewer_html, reels_html


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _download(self, code):
        try:
            data = media_info(code)
            url = data.get("video")
            if not url:
                return self._send(404, b"No video available for this post", "text/plain")
            # Resolve only Instagram media URLs, never arbitrary URLs from the client.
            parsed = urlsplit(url)
            host = parsed.hostname or ""
            if parsed.scheme != "https" or not any(
                    host.endswith("." + domain) or host == domain
                    for domain in ("cdninstagram.com", "fbcdn.net")):
                return self._send(502, b"Unexpected video host", "text/plain")
            with tempfile.TemporaryFile() as file, cr.Session(impersonate="chrome") as session:
                response = fetch_video(session, url)
                try:
                    if response.status_code != 200:
                        return self._send(502, b"Could not fetch video. Try again.", "text/plain")
                    for chunk in response.iter_content():
                        file.write(chunk)
                finally:
                    response.close()
                size = file.tell()
                if not size:
                    return self._send(502, b"Video was empty. Try again.", "text/plain")
                file.seek(0)
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(size))
                self.send_header("Content-Disposition", f'attachment; filename="{code}.mp4"')
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                shutil.copyfileobj(file, self.wfile)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Browser cancelled the download.
        except (Exception, SystemExit):
            return self._send(502, b"Download failed. Please try again.", "text/plain")

    def do_GET(self):
        if self.path.startswith("/download/"):
            code = self.path.removeprefix("/download/")
            if not re.fullmatch(r"[A-Za-z0-9_-]{5,30}", code):
                return self._send(400, b"invalid post code", "text/plain")
            return self._download(code)
        if self.path.startswith("/feeds?"):
            username = parse_qs(urlsplit(self.path).query).get("username", [""])[0].lstrip("@").lower()
            if not USER_RE.fullmatch(username):
                return self._send(400, b"bad username", "text/plain")
            feeds = load_feeds()
            rows = [{"name": name, "count": len(accounts_from_feed(feeds.get(name))),
                     "saved": username in accounts_from_feed(feeds.get(name))}
                    for name in sorted(set(feeds) | {"saved"})]
            return self._send(200, json.dumps(rows).encode(), "application/json")
        if self.path == "/blocked":
            return self._send(200, json.dumps(sorted(blocked_accounts())).encode(), "application/json")
        if self.path == "/feeds":
            return self._send(200, json.dumps(sorted(set(load_feeds()) | {"saved"})).encode(),
                              "application/json")
        if self.path == "/ping":
            return self._send(200, b"ok", "text/plain")
        if self.path in ("/", "/reels"):  # the one reels page: everything mixed
            try:
                batch = pool_more(feed_pool(MIX), 12)
                html = reels_html(batch["posts"])
                return self._send(200, html.encode(), "text/html; charset=utf-8")
            except (Exception, SystemExit) as e:
                return self._send(500, f"error: {e}".encode(), "text/plain")
        m = re.fullmatch(r"/u/([A-Za-z0-9._]+)", self.path)
        if m and USER_RE.match(m.group(1)):
            try:
                username = m.group(1).lower()
                state = STATE.get(username) or init_state(username)
                html = viewer_html(f"@{username} — logged-out feed",
                                   "50 more per click · click a post to zoom",
                                   state["posts"], username)
                return self._send(200, html.encode(), "text/html; charset=utf-8")
            except SystemExit as e:
                return self._send(502, str(e).encode(), "text/plain")
            except Exception as e:
                return self._send(500, f"error: {e}".encode(), "text/plain")
        legacy = {"/edits": "/"}
        if self.path in legacy:
            self.send_response(301)
            self.send_header("Location", legacy[self.path])
            self.end_headers()
            return
        return self._send(404, b"not found", "text/plain")

    def _media(self, code: str, pool: dict) -> dict:
        data = media_info(code)
        meme_accounts = set(accounts_from_feed(load_feeds().get("memes")))
        meme_source = data.get("owner") in meme_accounts | pool["meme_discoveries"]
        data.setdefault('shortcode', code)
        recommendations.remember_reels([data])
        # Saved creators bootstrap exploration; liked reels supply persistent leads.
        if data.get('owner') in feed_accounts() or recommendations.feedback_for([code]).get(code) == 'like':
            discover_into(pool, data.get('tagged_users') or [], meme_source=meme_source)
        data.pop('commenters', None)
        return data

    def do_POST(self):
        if self.path == "/feedback":
            try:
                length = int(self.headers.get('Content-Length') or 0)
                if not 0 < length <= 2048:
                    raise ValueError('invalid request size')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('invalid request')
                code = body.get('code')
                action = body.get('action')
                if not isinstance(code, str) or not re.fullmatch(r'[A-Za-z0-9_-]{5,30}', code):
                    raise ValueError('invalid post code')
                if not isinstance(action, str):
                    raise ValueError('invalid action')
                result = recommendations.set_feedback(code, action)
                return self._send(200, json.dumps({'feedback': result}).encode(), 'application/json')
            except (ValueError, TypeError):
                return self._send(400, b'invalid feedback', 'text/plain')
            except KeyError:
                return self._send(404, b'load this reel first', 'text/plain')
            except Exception:
                return self._send(500, b'could not save feedback', 'text/plain')
        if self.path.startswith("/recognize/"):
            code = self.path.removeprefix("/recognize/")
            if not re.fullmatch(r"[A-Za-z0-9_-]{5,30}", code):
                return self._send(400, b"invalid post code", "text/plain")
            result = recognize_song(code)
            recommendations.remember_song(code, result)
            return self._send(200, json.dumps(result).encode(), "application/json")
        if self.path in ("/block", "/unblock"):
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if not 0 < length <= 1024:
                    raise ValueError("bad body length")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict) or not isinstance(body.get("username"), str):
                    raise ValueError("invalid username")
                blocked = set_blocked(body["username"], self.path == "/block")
                return self._send(200, json.dumps({"blocked": sorted(blocked)}).encode(), "application/json")
            except (ValueError, TypeError):
                return self._send(400, b"invalid block request", "text/plain")
            except OSError:
                return self._send(500, b"could not save blocklist", "text/plain")
        if self.path == "/preferences":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if not 0 < length <= 1024:
                    raise ValueError("bad body length")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict) or type(body.get("prefer_memes")) is not bool:
                    raise ValueError("prefer_memes must be true or false")
                set_prefer_memes(body["prefer_memes"])
                return self._send(200, json.dumps(body).encode(), "application/json")
            except (ValueError, TypeError):
                return self._send(400, b"invalid preference", "text/plain")
            except OSError:
                return self._send(500, b"could not save preference", "text/plain")
        if self.path == "/remove":  # remove account from every feed
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                username = str(body.get("username", "")).lstrip("@").lower()
                if not USER_RE.fullmatch(username):
                    return self._send(400, b"bad username", "text/plain")
                feeds = load_feeds()
                removed = False
                for name, val in feeds.items():
                    accounts = accounts_from_feed(val)
                    if username in accounts:
                        accounts.remove(username)
                        feeds[name] = accounts
                        removed = True
                if removed:
                    save_feeds(feeds)  # mix pool hot-reloads
                return self._send(200, json.dumps(
                    {"ok": True, "removed": removed}).encode(),
                    "application/json")
            except Exception as e:
                return self._send(500, json.dumps({"error": str(e)}).encode(),
                                  "application/json")
        if self.path == "/add":  # add account to the "saved" feed (the mix)
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                username = str(body.get("username", "")).lstrip("@").lower()
                if not USER_RE.fullmatch(username):
                    return self._send(400, b"bad username", "text/plain")
                feeds = load_feeds()
                destination = str(body.get("feed", "")).lower()
                if not FEED_RE.fullmatch(destination) or destination not in set(feeds) | {"saved"}:
                    return self._send(400, b"choose a valid feed", "text/plain")
                cur = accounts_from_feed(feeds.get(destination))
                if username not in cur:
                    feeds[destination] = cur + [username]
                    save_feeds(feeds)  # mix pool hot-reloads on mtime change
                    return self._send(200, json.dumps(
                        {"ok": True, "added": username}).encode(),
                        "application/json")
                return self._send(200, json.dumps(
                    {"ok": True, "duplicate": True}).encode(),
                    "application/json")
            except Exception as e:
                return self._send(500, json.dumps({"error": str(e)}).encode(),
                                  "application/json")
        m = re.fullmatch(r"/u/([A-Za-z0-9._]+)/(more|media)", self.path)
        rm = re.fullmatch(r"/(reels|all)/(more|media)", self.path)
        username = action = None
        pool = feed_pool(MIX)  # one pool: the mix (discovery included)
        if m and USER_RE.match(m.group(1)):
            username, action = m.group(1).lower(), m.group(2)
        elif rm:
            username, action = MIX, rm.group(2)
        else:
            return self._send(404, b"not found", "text/plain")
        try:
            if action == "more":
                if username == MIX:
                    length = int(self.headers.get("Content-Length") or 0)
                    if length < 0 or length > 65536:
                        return self._send(400, b"bad body length", "text/plain")
                    body = json.loads(self.rfile.read(length) or b"{}")
                    excluded = body.get("session_blocked", []) if isinstance(body, dict) else None
                    if not isinstance(excluded, list) or len(excluded) > 1000 or any(
                            not isinstance(u, str) or not USER_RE.fullmatch(u) for u in excluded):
                        return self._send(400, b"invalid session blocklist", "text/plain")
                    data = pool_more(pool, excluded={u.lower() for u in excluded})
                else:
                    data = more_posts(username)
                if username == MIX:
                    # keep the client's ✓/➕ state in sync across tabs/CLI
                    data["feed_accounts"] = sorted(feed_accounts())
                    data["blocked"] = sorted(blocked_accounts())
            else:  # media: body {"code": "<shortcode>"}
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                code = str(body.get("code", ""))
                if not re.fullmatch(r"[A-Za-z0-9_-]{5,30}", code):
                    return self._send(400, b"bad code", "text/plain")
                data = self._media(code, pool)
            return self._send(200, json.dumps(data).encode(),
                              "application/json")
        except (Exception, SystemExit) as e:
            return self._send(500, json.dumps({"error": str(e)}).encode(),
                              "application/json")


def serve(port: int) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    SERVER_JSON.write_text(json.dumps(
        {"port": srv.server_address[1], "pid": __import__("os").getpid()}))
    sys.stderr.write(f"ig server on http://127.0.0.1:{srv.server_address[1]}\n")
    srv.serve_forever()


def _alive(port: int) -> bool:
    from curl_cffi import requests as _r
    try:
        return _r.get(f"http://127.0.0.1:{port}/ping", timeout=2).status_code == 200
    except Exception:
        return False


def ensure_server() -> int:
    if SERVER_JSON.exists():
        try:
            info = json.loads(SERVER_JSON.read_text())
            if _alive(info["port"]):
                return info["port"]
        except (json.JSONDecodeError, KeyError):
            pass
    import socket
    with socket.socket() as sk:  # pick a free port
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    subprocess.Popen([sys.executable, "-m", "ig", "serve", "--port", str(port)],
                     start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):  # wait up to 5s
        if _alive(port):
            return port
        time.sleep(0.1)
    raise SystemExit("server failed to start")


def open_in_browser(url: str) -> bool:
    import shutil
    try:
        r = subprocess.run(["xdg-open", url], capture_output=True, timeout=25)
        if r.returncode == 0:
            return True
    except Exception:
        pass
    try:
        if subprocess.run(["gio", "open", url], capture_output=True,
                          timeout=25).returncode == 0:
            return True
    except Exception:
        pass
    for browser in ("firefox", "librewolf", "chromium", "google-chrome",
                    "google-chrome-stable", "brave-browser"):
        exe = shutil.which(browser)
        if exe:
            subprocess.Popen([exe, url], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return True
    return False


def open_user(target: str) -> None:
    """target: a username or the literal path '/reels'."""
    port = ensure_server()
    path = target if target.startswith("/") else f"/u/{target}"
    url = f"http://127.0.0.1:{port}{path}"
    print(f"{url}", file=sys.stderr)
    if open_in_browser(url):
        print("opened in your browser", file=sys.stderr)
    else:
        print("couldn't auto-open a browser — open the URL above", file=sys.stderr)


def stop_server() -> None:
    import os
    import signal
    if SERVER_JSON.exists():
        info = json.loads(SERVER_JSON.read_text())
        try:
            os.kill(info["pid"], signal.SIGTERM)
            print("stopped", file=sys.stderr)
        except (ProcessLookupError, PermissionError):
            print("already stopped", file=sys.stderr)
        SERVER_JSON.unlink()
    else:
        print("not running", file=sys.stderr)
