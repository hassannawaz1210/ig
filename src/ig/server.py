from . import recommendations
from .media import fetch_video
import json
import logging
import os
import shutil
import tempfile
from curl_cffi import requests as cr
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from . import logs
from .config import CACHE, SERVER_JSON, USER_RE, FEED_RE, load_feeds, edit_feeds, accounts_from_feed, set_prefer_memes, selected_feeds, set_selected_feeds, blocked_accounts, set_blocked
from .feeds import MIX, feed_pool, pool_more, feed_accounts
from .instagram import (load_profile, more_posts, media_info, InstagramError, RateLimited,
                        ProfileUnavailable, NetworkError)
from .recognition import recognize_song
from .views import viewer_html, reels_html, static_file

log = logging.getLogger(__name__)

INTERNAL_ERROR = "Something went wrong. Details are in the server log (run: ig logs)."


def error_status(error: Exception) -> tuple[int, str]:
    """HTTP status and a message that is safe to show in the browser."""
    if isinstance(error, RateLimited):
        return 429, str(error)
    if isinstance(error, ProfileUnavailable):
        return 404, str(error)
    if isinstance(error, NetworkError):
        return 503, str(error)
    if isinstance(error, InstagramError):
        return 502, str(error)
    return 500, INTERNAL_ERROR


class Handler(BaseHTTPRequestHandler):
    # Set to a PhoneAccess object when serving other devices on the network.
    access = None

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.address_string(), fmt % args)

    def _send(self, code: int, body: bytes, ctype: str, headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _fail(self, error: Exception, *, as_json: bool = False, context: str = ""):
        status, message = error_status(error)
        if status == 500:
            log.error("Unexpected error: %s", context or self.path, exc_info=error)
        else:
            log.info("%s failed: %s", context or self.path, error)
        if as_json:
            return self._send(status, json.dumps({"error": message}).encode(), "application/json")
        return self._send(status, message.encode(), "text/plain; charset=utf-8")

    def _allowed(self) -> bool:
        return self.access is None or self.access.allow(self)

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
                log.warning("Refused video from unexpected host %s", host)
                return self._send(502, b"Unexpected video host", "text/plain")
            with tempfile.TemporaryFile() as file, cr.Session(impersonate="chrome") as session:
                response = fetch_video(session, url)
                try:
                    if response.status_code != 200:
                        log.warning("Video download for %s returned HTTP %s", code, response.status_code)
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
        except InstagramError as error:
            log.info("Download of %s failed: %s", code, error)
            return self._send(502, b"Download failed. Please try again.", "text/plain")
        except Exception:
            log.exception("Unexpected error downloading %s", code)
            return self._send(502, b"Download failed. Please try again.", "text/plain")

    def do_GET(self):
        if self.path == "/ping":
            return self._send(200, b"ok", "text/plain")
        if not self._allowed():
            return
        if self.path.startswith("/static/"):
            found = static_file(self.path.removeprefix("/static/"))
            if found is None:
                return self._send(404, b"not found", "text/plain")
            return self._send(200, *found)
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
        if self.path == "/preferences":
            return self._send(200, json.dumps({"selected_feeds": selected_feeds(),
                                              "feeds": list(load_feeds())}).encode(), "application/json")
        if self.path in ("/", "/reels"):  # the one reels page: everything mixed
            # Send the page at once; it asks POST /reels/more for its first
            # batch, which can take several seconds of Instagram requests.
            try:
                html = reels_html()
                return self._send(200, html.encode(), "text/html; charset=utf-8")
            except Exception as error:
                return self._fail(error, context="Opening the reels page")
        m = re.fullmatch(r"/u/([A-Za-z0-9._]+)", self.path)
        if m and USER_RE.match(m.group(1)):
            username = m.group(1).lower()
            try:
                state = load_profile(username)
                html = viewer_html(f"@{username} — logged-out feed",
                                   "50 more per click · click a post to zoom",
                                   state["posts"], username)
                return self._send(200, html.encode(), "text/html; charset=utf-8")
            except Exception as error:
                return self._fail(error, context=f"Opening @{username}")
        legacy = {"/edits": "/"}
        if self.path in legacy:
            self.send_response(301)
            self.send_header("Location", legacy[self.path])
            self.end_headers()
            return
        return self._send(404, b"not found", "text/plain")

    def _media(self, code: str) -> dict:
        data = media_info(code)
        data.setdefault('shortcode', code)
        recommendations.remember_reels([data])
        # Discovery examines stored creator credits and validates candidates;
        # arbitrary tags and sponsor mentions are not endorsements.
        data.pop('commenters', None)
        return data

    def do_POST(self):
        if self.access is not None and self.path == "/sign-in":
            return self.access.sign_in(self)
        if not self._allowed():
            return
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
                log.exception("Could not save feedback")
                return self._send(500, b'could not save feedback', 'text/plain')
        if self.path.startswith("/recognize/"):
            code = self.path.removeprefix("/recognize/")
            if not re.fullmatch(r"[A-Za-z0-9_-]{5,30}", code):
                return self._send(400, b"invalid post code", "text/plain")
            result = recognize_song(code)
            try:
                recommendations.remember_song(code, result)
            except Exception:
                log.exception("Could not store the song for %s", code)  # The result is still useful.
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
            except Exception:
                log.exception("Could not save the blocklist")
                return self._send(500, b"could not save blocklist", "text/plain")
        if self.path == "/preferences":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if not 0 < length <= 1024:
                    raise ValueError("bad body length")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("invalid preference")
                if "selected_feeds" in body:
                    set_selected_feeds(body["selected_feeds"])
                    body = {"selected_feeds": selected_feeds()}
                elif type(body.get("prefer_memes")) is bool:
                    # Compatibility for already-open tabs from the old UI.
                    set_prefer_memes(body["prefer_memes"])
                    set_selected_feeds(["memes"] if body["prefer_memes"] and "memes" in load_feeds() else None)
                else:
                    raise ValueError("choose selected_feeds")
                return self._send(200, json.dumps(body).encode(), "application/json")
            except (ValueError, TypeError):
                return self._send(400, b"invalid preference", "text/plain")
            except Exception:
                log.exception("Could not save the feed selection")
                return self._send(500, b"could not save preference", "text/plain")
        if self.path == "/remove":  # remove account from every feed
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                username = str(body.get("username", "")).lstrip("@").lower()
                if not USER_RE.fullmatch(username):
                    return self._send(400, b"bad username", "text/plain")
                with edit_feeds() as feeds:
                    removed = False
                    for name, val in feeds.items():
                        accounts = accounts_from_feed(val)
                        if username in accounts:
                            accounts.remove(username)
                            feeds[name] = accounts
                            removed = True
                return self._send(200, json.dumps(
                    {"ok": True, "removed": removed}).encode(),
                    "application/json")
            except (ValueError, TypeError, AttributeError):
                return self._send(400, b"invalid request", "text/plain")
            except Exception as error:
                return self._fail(error, as_json=True, context="Removing an account")
        if self.path == "/add":  # add account to the "saved" feed (the mix)
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                username = str(body.get("username", "")).lstrip("@").lower()
                if not USER_RE.fullmatch(username):
                    return self._send(400, b"bad username", "text/plain")
                with edit_feeds() as feeds:
                    destination = str(body.get("feed", "")).lower()
                    if not FEED_RE.fullmatch(destination) or destination not in set(feeds) | {"saved"}:
                        return self._send(400, b"choose a valid feed", "text/plain")
                    cur = accounts_from_feed(feeds.get(destination))
                    duplicate = username in cur
                    if not duplicate:
                        feeds[destination] = cur + [username]
                result = {"ok": True, "duplicate": True} if duplicate else {"ok": True, "added": username}
                return self._send(200, json.dumps(result).encode(), "application/json")
            except (ValueError, TypeError, AttributeError):
                return self._send(400, b"invalid request", "text/plain")
            except Exception as error:
                return self._fail(error, as_json=True, context="Adding an account")
        m = re.fullmatch(r"/u/([A-Za-z0-9._]+)/(more|media)", self.path)
        rm = re.fullmatch(r"/(reels|all)/(more|media)", self.path)
        username = action = None
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
                    data = pool_more(feed_pool(MIX), excluded={u.lower() for u in excluded})
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
                data = self._media(code)
            return self._send(200, json.dumps(data).encode(),
                              "application/json")
        except (ValueError, TypeError, AttributeError) as error:
            log.info("Bad request to %s: %s", self.path, error)
            return self._send(400, json.dumps({"error": "invalid request"}).encode(), "application/json")
        except Exception as error:
            return self._fail(error, as_json=True)


def make_server(host: str, port: int, access=None) -> ThreadingHTTPServer:
    handler = Handler if access is None else type("SharedHandler", (Handler,), {"access": access})
    return ThreadingHTTPServer((host, port), handler)


def serve(port: int) -> None:
    """The local background server; only this computer can reach it."""
    CACHE.mkdir(parents=True, exist_ok=True)
    logs.setup(console=sys.stderr.isatty())
    srv = make_server("127.0.0.1", port)
    SERVER_JSON.write_text(json.dumps(
        {"port": srv.server_address[1], "pid": os.getpid()}))
    log.info("ig server on http://127.0.0.1:%d (log: %s)", srv.server_address[1], logs.log_file())
    try:
        srv.serve_forever()
    finally:
        log.info("ig server stopped")


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
    raise SystemExit(f"server failed to start; see the log: {logs.log_file()}")


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
