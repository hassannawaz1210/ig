from . import recommendations, config, history, following, backup
from .media import fetch_video
import json
import math
import logging
import os
import shutil
import tempfile
import threading
from curl_cffi import requests as cr
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit, parse_qs, unquote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from collections import OrderedDict
from . import logs
from . import instagram
from .config import CACHE, SERVER_JSON, USER_RE, FEED_RE, load_feeds, edit_feeds, accounts_from_feed, set_prefer_memes, selected_feeds, set_selected_feeds, blocked_accounts, set_blocked
from .feeds import MIX, feed_pool, pool_more, feed_accounts, creator_more
from .instagram import (load_profile, more_posts, InstagramError, RateLimited,
                        ProfileUnavailable, NetworkError, PROFILES, STATE)
from .reels import REELS, media_info
from . import dash
from .topics import TOPICS
from .suggestions import SUGGESTIONS
from .views import viewer_html, reels_html, settings_html, feeds_html, stats_html, static_file, status_html
from . import stats

log = logging.getLogger(__name__)

INTERNAL_ERROR = "Something went wrong. Details are in the server log (run: ig logs)."
POST_CODE = re.compile(r"[A-Za-z0-9_-]{5,30}")
MAX_SAVED_REEL_BODY = 1024
SAVED_REEL_DETAILS = ("caption", "url", "taken_at", "like_count", "comment_count", "image")

# Pagination cursors of open sound pages, bounded; one sound per slot.
SOUND_STATE = OrderedDict()
SOUND_LOCK = threading.Lock()
MAX_SOUND_PAGES = 32


def _remember_sound(audio_id: str, data: dict) -> None:
    with SOUND_LOCK:
        SOUND_STATE[audio_id] = (data.get("cursor"), data.get("has_more") is True)
        SOUND_STATE.move_to_end(audio_id)
        while len(SOUND_STATE) > MAX_SOUND_PAGES:
            SOUND_STATE.popitem(last=False)


def sound_more(audio_id: str) -> dict:
    """The next page of a sound's reels for an already opened sound page."""
    with SOUND_LOCK:
        cursor, has_more = SOUND_STATE.get(audio_id, (None, False))
    if not has_more or not cursor:
        return {"posts": [], "has_more": False}
    data = instagram.audio_reels(audio_id, after=cursor)
    _remember_sound(audio_id, data)
    return {"posts": data["reels"], "has_more": data["has_more"],
            "total": data["total"]}


def saved_reel_details(code: str) -> dict:
    """Display metadata for a loaded reel, from profiles already in memory or
    on disk. Never contacts Instagram; returns {} when nothing is known."""
    reel = recommendations.stored_reel(code)
    if reel is None:
        raise KeyError("Reel has not been loaded")
    owner = reel.get("owner")
    if not isinstance(owner, str) or not USER_RE.fullmatch(owner):
        return {}
    try:
        state = REELS.cached(owner) or STATE.get(owner) or PROFILES.cached(owner)
    except Exception:
        # The reel can still be saved with the metadata stored for it.
        log.warning("Could not read the cached profile of @%s for saved reel %s", owner, code, exc_info=True)
        return {}
    for post in (state or {}).get("posts", []):
        if post.get("shortcode") == code:
            return {key: post.get(key) for key in SAVED_REEL_DETAILS}
    return {}


COMMENT_WORKERS = threading.BoundedSemaphore(2)
STREAM_WORKERS = threading.BoundedSemaphore(2)


def _schedule_stream_assembly(code: str, manifest: str) -> None:
    """Pre-assemble a reel's best-quality stream in the background.

    The /media response returns immediately; by the time the browser requests
    /video/<code> for a preloaded slide, the file is usually already on disk."""
    if dash.cached(code) is not None:
        return

    def build():
        if not STREAM_WORKERS.acquire(blocking=False):
            return  # /video assembles synchronously when the worker is busy.
        try:
            dash.assemble(code, manifest)
        except Exception:
            log.exception("Could not pre-assemble %s", code)
        finally:
            STREAM_WORKERS.release()

    threading.Thread(target=build, daemon=True).start()


def _schedule_comment_enrichment(code: str) -> None:
    """Fetch one reel's comments in the background for discovery leads.

    The media response must not wait for a second Instagram request, and each
    reel's comments are only fetched once; failures are skipped, not retried."""
    if recommendations.stored_reel(code).get('commenters') is not None:
        return

    def enrich():
        if not COMMENT_WORKERS.acquire(blocking=False):
            return
        try:
            recommendations.remember_commenters(code, instagram.post_comments(code))
        except InstagramError as error:
            log.info("Comment enrichment skipped for %s: %s", code, error)
        except Exception:
            log.warning("Comment enrichment failed for %s", code, exc_info=True)
        finally:
            COMMENT_WORKERS.release()

    threading.Thread(target=enrich, daemon=True).start()


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
        title = "Couldn’t open this page"
        if self.path.startswith('/u/'):
            title = "Account unavailable" if status == 404 else "Couldn’t load account"
        if status == 500:
            message = "Something went wrong. Try again in a moment."
        return self._send(status, status_html(title, message).encode(), "text/html; charset=utf-8")

    def _allowed(self) -> bool:
        return self.access is None or self.access.allow(self)

    def _download(self, code, slide=None):
        try:
            if slide is not None:
                data = media_info(code)
                items = data.get('slides') or []
                if slide >= len(items):
                    return self._send(404, b'Post item not found', 'text/plain')
                return self._download_progressive(f'{code}-{slide + 1}', items[slide])
            # Saved files get the same best-quality stream as playback.
            path = dash.cached(code)
            if path is None:
                data = media_info(code)
                manifest = data.get("video_dash_manifest")
                if manifest:
                    path = dash.assemble(code, manifest)
                if path is None:
                    return self._download_progressive(code, data)
            return self._send_video_file(path, attachment=f"{code}.mp4")
        except (BrokenPipeError, ConnectionResetError):
            pass  # Browser cancelled the download.
        except InstagramError as error:
            log.info("Download of %s failed: %s", code, error)
            return self._send(502, b"Download failed. Please try again.", "text/plain")
        except Exception:
            log.exception("Unexpected error downloading %s", code)
            return self._send(502, b"Download failed. Please try again.", "text/plain")

    def _download_progressive(self, code, data):
        is_image = not data.get('video')
        url = data.get('video') or data.get('image')
        if not url:
            return self._send(404, b'No downloadable media for this post', 'text/plain')
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
                mime = response.headers.get('Content-Type', '').split(';')[0].lower()
                image_types = {'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'image/avif': 'avif'}
                if is_image and mime not in image_types:
                    return self._send(502, b'Unexpected image format', 'text/plain')
                extension = image_types[mime] if is_image else 'mp4'
                for chunk in response.iter_content():
                    file.write(chunk)
                    if is_image and file.tell() > 50 * 1024 * 1024:
                        return self._send(502, b'Image is too large to download', 'text/plain')
            finally:
                response.close()
            size = file.tell()
            if not size:
                return self._send(502, b"Video was empty. Try again.", "text/plain")
            file.seek(0)
            self.send_response(200)
            self.send_header("Content-Type", mime if is_image else "video/mp4")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f'attachment; filename="{code}.{extension}"')
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            shutil.copyfileobj(file, self.wfile)

    def _video(self, code):
        """Serve a reel's assembled best-quality file, or fall back."""
        try:
            entry = dash.recall(code) or {}
            path = dash.cached(code)
            if path is None:
                entry = dash.recall(code)
                if entry is None:
                    data = media_info(code)
                    entry = {"mpd": data.get("video_dash_manifest") or "",
                             "fallback": data.get("video")}
                try:
                    path = dash.assemble(code, entry.get("mpd") or "")
                except Exception:
                    log.exception("Could not assemble stream %s", code)
                    path = None
            if path is None:
                fallback = entry.get("fallback")
                if fallback:
                    self.send_response(302)
                    self.send_header("Location", fallback)
                    self.end_headers()
                    return
                return self._send(502, b"Could not load this video. Try again.", "text/plain")
            self._send_video_file(path)
        except (BrokenPipeError, ConnectionResetError):
            pass  # The browser cancelled the request.
        except InstagramError as error:
            log.info("Stream of %s failed: %s", code, error)
            return self._send(502, b"Could not load this video. Try again.", "text/plain")
        except Exception:
            log.exception("Unexpected stream failure for %s", code)
            return self._send(502, b"Could not load this video. Try again.", "text/plain")

    def _send_video_file(self, path, attachment=None):
        """One assembled file, honouring a single Range header for seeking."""
        size = os.path.getsize(path)
        start, end = 0, size - 1
        status = 200
        header = self.headers.get("Range")
        if header:
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", header.strip())
            if match:
                start = int(match.group(1))
                if match.group(2):
                    end = min(int(match.group(2)), size - 1)
                if start > end or start >= size:
                    return self._send(416, b"range not satisfiable", "text/plain")
                status = 206
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        if attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{attachment}"')
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with path.open("rb") as file:
            file.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = file.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_GET(self):
        if self.path == "/ping":
            return self._send(200, b"ok", "text/plain")
        if not self._allowed():
            return
        if self.path == '/app/update/check':
            html = status_html('Check for updates',
                'This version cannot check for updates here. Download the latest Android app to enable in-app updates.', retry=False)
            return self._send(200, html.encode(), 'text/html; charset=utf-8')
        if self.path == '/stats' or self.path.startswith('/stats/data'):
            try:
                if self.path == '/stats':
                    return self._send(200, stats_html().encode(), 'text/html; charset=utf-8')
                query = parse_qs(urlsplit(self.path).query)
                days = (query.get('days') or ['30'])[0]
                if days not in ('7', '30', '0'):
                    return self._send(400, b'{"error":"Choose 7, 30 or 0 days"}', 'application/json')
                stats.backfill()
                return self._send(200, json.dumps(stats.compute(int(days))).encode(), 'application/json')
            except Exception as error:
                return self._fail(error, as_json=self.path != '/stats', context='Loading your stats')
        if self.path in ('/settings', '/settings/feeds', '/settings/data'):
            try:
                if self.path == '/settings':
                    return self._send(200, settings_html().encode(), 'text/html; charset=utf-8')
                if self.path == '/settings/feeds':
                    return self._send(200, feeds_html().encode(), 'text/html; charset=utf-8')
                return self._send(200, json.dumps({'feeds': load_feeds(),
                    'feed_topics': config.feed_topic_words(),
                    'selected_feeds': selected_feeds(),
                    'blocked_accounts': sorted(blocked_accounts()),
                    'scroll_animation': config.scroll_animation(),
                    **config.playback_preferences(include_missing=True)}).encode(), 'application/json')
            except Exception as error:
                return self._fail(error, as_json=True, context='Loading settings')
        if self.path == '/feeds/export':
            try:
                filename = 'ig-feeds-' + time.strftime('%Y-%m-%d') + '.json'
                return self._send(200, backup.export(), 'application/json',
                                  {'Content-Disposition': f'attachment; filename="{filename}"'})
            except backup.TooLarge as error:
                return self._send(413, json.dumps({'error': str(error)}).encode(), 'application/json')
            except Exception as error:
                return self._fail(error, as_json=True, context='Exporting feeds')
        if self.path.startswith("/static/"):
            found = static_file(self.path.removeprefix("/static/"))
            if found is None:
                return self._send(404, b"not found", "text/plain")
            return self._send(200, *found)
        if self.path.startswith("/download/"):
            code = urlsplit(self.path).path.removeprefix("/download/")
            if not re.fullmatch(r"[A-Za-z0-9_-]{5,30}", code):
                return self._send(400, b"invalid post code", "text/plain")
            values = parse_qs(urlsplit(self.path).query).get('slide')
            if values and (len(values) != 1 or not re.fullmatch(r'[0-9]{1,2}', values[0])):
                return self._send(400, b'invalid carousel item', 'text/plain')
            return self._download(code, int(values[0]) if values else None)
        if self.path.startswith("/video/"):
            code = self.path.removeprefix("/video/")
            if not re.fullmatch(r"[A-Za-z0-9_-]{5,30}", code):
                return self._send(400, b"invalid post code", "text/plain")
            return self._video(code)
        if self.path.startswith("/feeds?"):
            username = parse_qs(urlsplit(self.path).query).get("username", [""])[0].lstrip("@").lower()
            if not USER_RE.fullmatch(username):
                return self._send(400, b"bad username", "text/plain")
            feeds = load_feeds()
            rows = [{"name": name, "count": len(accounts_from_feed(feeds.get(name))),
                     "saved": username in accounts_from_feed(feeds.get(name))}
                    for name in sorted(feeds)]
            return self._send(200, json.dumps(rows).encode(), "application/json")
        if self.path == "/blocked":
            return self._send(200, json.dumps(sorted(blocked_accounts())).encode(), "application/json")
        if self.path == "/feeds":
            return self._send(200, json.dumps(sorted(load_feeds())).encode(),
                              "application/json")
        if self.path == "/preferences":
            return self._send(200, json.dumps({"selected_feeds": selected_feeds(),
                                              "feeds": list(load_feeds())}).encode(), "application/json")
        if self.path == "/saved-reels":
            try:
                reels = recommendations.saved_reels()
                html = viewer_html("Saved reels", "reels you saved with S · click one to play",
                                   reels, paged=False,
                                   empty_message="No saved reels yet. Press S on a reel to save it.")
                return self._send(200, html.encode(), "text/html; charset=utf-8")
            except Exception as error:
                return self._fail(error, context="Opening Saved reels")
        direct = re.fullmatch(r"/(?:reel|p)/([A-Za-z0-9_-]{5,30})/?", urlsplit(self.path).path)
        if direct or self.path in ("/", "/reels"):  # the one reels page: everything mixed
            # Send the page at once; it asks POST /reels/more for its first
            # batch, which can take several seconds of Instagram requests.
            try:
                html = reels_html(direct.group(1)) if direct else reels_html()
                return self._send(200, html.encode(), "text/html; charset=utf-8")
            except Exception as error:
                return self._fail(error, context="Opening the reels page")
        m = re.fullmatch(r"/u/([A-Za-z0-9._]+)", self.path)
        if m and USER_RE.match(m.group(1)):
            username = m.group(1).lower()
            try:
                state = load_profile(username)
                html = viewer_html(f"@{username}",
                                   "Posts",
                                   state["posts"], username, profile=state.get("profile") or {"biography": state.get("biography", "")})
                return self._send(200, html.encode(), "text/html; charset=utf-8")
            except Exception as error:
                return self._fail(error, context=f"Opening @{username}")
        m = re.fullmatch(r"/related/([A-Za-z0-9._]+)", self.path)
        if m and USER_RE.match(m.group(1)):
            username = m.group(1).lower()
            try:
                return self._send(200, json.dumps(
                    {"accounts": instagram.related_accounts(username)}).encode(),
                    "application/json")
            except Exception as error:
                return self._fail(error, as_json=True, context=f"Loading suggestions for @{username}")
        m = re.fullmatch(r"/sound/(\d{10,32})", self.path)
        if m:
            audio_id = m.group(1)
            try:
                data = instagram.audio_reels(audio_id)
                _remember_sound(audio_id, data)
                html = viewer_html(data["title"],
                                   data["total"] or "Reels using this sound",
                                   data["reels"],
                                   empty_message="" if data["reels"] else
                                   "Instagram returned no public reels for this sound",
                                   sound={"id": audio_id, "title": data["title"], "total": data["total"]})
                return self._send(200, html.encode(), "text/html; charset=utf-8")
            except Exception as error:
                return self._fail(error, context=f"Opening the sound {audio_id}")
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
        data['saved'] = code in recommendations.saved_codes([code])
        data['feedback'] = recommendations.feedback_for([code]).get(code)
        manifest = data.pop('video_dash_manifest', None)
        if not config.ANDROID and manifest and dash.plan(manifest):
            # Play the locally assembled best-quality stream instead of the
            # 720p progressive rendition; the browser resolves this relative
            # URL against the page. The progressive URL stays the fallback.
            dash.stash(code, manifest, data['video'])
            data['video'] = f'/video/{code}'
            _schedule_stream_assembly(code, manifest)
        _schedule_comment_enrichment(code)
        # Discovery examines stored creator credits and validates candidates;
        # arbitrary tags, commenters and sponsor mentions are not endorsements.
        data.pop('commenters', None)
        data.pop('reply_mentions', None)
        return data

    def _set_reel_saved(self):
        """POST /saved-reels {code, saved}: save or unsave one reel locally."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= MAX_SAVED_REEL_BODY:
                raise ValueError("invalid request size")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("invalid request")
            code = body.get("code")
            saved = body.get("saved")
            if not isinstance(code, str) or not POST_CODE.fullmatch(code):
                raise ValueError("invalid post code")
            if type(saved) is not bool:
                raise ValueError("saved must be true or false")
            details = saved_reel_details(code) if saved else None
            result = recommendations.set_reel_saved(code, saved, details)
            return self._send(200, json.dumps({"saved": result}).encode(), "application/json")
        except (ValueError, TypeError) as error:
            log.info("Bad Saved reels request: %s", error)
            return self._send(400, b"invalid Saved reels request", "text/plain")
        except KeyError:
            return self._send(404, b"load this reel first", "text/plain")
        except Exception:
            log.exception("Could not update Saved reels")
            return self._send(500, b"could not update Saved reels", "text/plain")

    def _import_feeds(self):
        try:
            length = int(self.headers.get('Content-Length') or 0)
            if length > backup.MAX_BYTES:
                self.close_connection = True
                raise backup.TooLarge('Feed backups must be 5 MB or smaller.')
            if length <= 0:
                self.close_connection = True
                raise ValueError('Choose an ig feed backup.')
            data = self.rfile.read(length)
            if len(data) != length:
                raise ValueError('The upload was incomplete. Please try again.')
            document = backup.parse(data)
            result = backup.summary(document) if self.path.endswith('/preview') else backup.merge(document)
            return self._send(200, json.dumps(result).encode(), 'application/json')
        except backup.TooLarge as error:
            return self._send(413, json.dumps({'error': str(error)}).encode(), 'application/json')
        except (ValueError, TypeError) as error:
            return self._send(400, json.dumps({'error': str(error)}).encode(), 'application/json')
        except Exception as error:
            return self._fail(error, as_json=True, context='Importing feeds')

    def _following(self):
        """Preview an export or atomically add its selected handles to one feed."""
        try:
            limit = following.MAX_UPLOAD if self.path == '/following/preview' else 500_000
            length = int(self.headers.get('Content-Length') or 0)
            if length > limit:
                self.close_connection = True
                raise following.TooLarge('Upload is too large (25 MB maximum). Extract following.json or following.html from the ZIP.'
                                         if self.path == '/following/preview' else 'Import request is too large.')
            if length <= 0:
                self.close_connection = True
                raise ValueError('Choose a following export.' if self.path == '/following/preview' else 'Empty import request.')
            data = self.rfile.read(length)
            if len(data) != length:
                raise ValueError('The upload was incomplete. Please try again.')
            if self.path == '/following/preview':
                filename = unquote(self.headers.get('X-File-Name', ''))
                result = {'accounts': following.parse_export(data, filename)}
            else:
                body = json.loads(data)
                if not isinstance(body, dict) or not isinstance(body.get('name'), str):
                    raise ValueError('Enter a feed name.')
                name = re.sub(r'\s+', '_', body['name'].strip().lower())
                if not FEED_RE.fullmatch(name):
                    raise ValueError('Use 1–20 letters, numbers, spaces, underscores or hyphens.')
                create = body.get('create', False)
                entries = body.get('accounts')
                if not isinstance(create, bool) or not isinstance(entries, list) or not entries:
                    raise ValueError('Select at least one account to import.')
                if len(entries) > following.MAX_ACCOUNTS:
                    raise following.TooLarge('Import at most 7,500 accounts at once.')
                if any(not isinstance(value, str) or not USER_RE.fullmatch(value) for value in entries):
                    raise ValueError('Use Instagram handles only.')
                accounts = list(dict.fromkeys(value.lower() for value in entries))
                with config.database() as db:
                    feeds = config._read_feeds(db)
                    blocked = {row[0] for row in db.execute('SELECT username FROM blocked_accounts')}
                    skipped = sum(account in blocked for account in accounts)
                    allowed = [account for account in accounts if account not in blocked]
                    if create and name in feeds:
                        return self._send(409, b'{"error":"That feed already exists"}', 'application/json')
                    if not create and name not in feeds:
                        raise ValueError('Choose an existing feed.')
                    previous = accounts_from_feed(feeds.get(name))
                    previous_set = set(previous)
                    added = [account for account in allowed if account not in previous_set]
                    feeds[name] = previous + added
                    config._write_feeds(db, feeds)
                result = {'name': name, 'created': create, 'accounts': added,
                          'added': len(added), 'existing': len(allowed) - len(added), 'blocked': skipped}
            return self._send(200, json.dumps(result).encode(), 'application/json')
        except following.TooLarge as error:
            return self._send(413, json.dumps({'error': str(error)}).encode(), 'application/json')
        except (ValueError, TypeError, RecursionError) as error:
            return self._send(400, json.dumps({'error': str(error)}).encode(), 'application/json')
        except Exception as error:
            return self._fail(error, as_json=True, context='Importing following')

    def do_POST(self):
        if self.access is not None and self.path == "/sign-in":
            return self.access.sign_in(self)
        if not self._allowed():
            return
        if self.path in ('/settings/scroll', '/settings/playback', '/feeds/accounts/remove', '/feeds/topics'):
            try:
                length = int(self.headers.get('Content-Length') or 0)
                if not 0 < length <= 4096:
                    raise ValueError('Invalid request size.')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('Invalid request.')
                if self.path == '/settings/scroll':
                    mode = body.get('mode')
                    config.set_scroll_animation(mode)
                    result = {'scroll_animation': mode}
                elif self.path == '/settings/playback':
                    migrate = body.pop('migrate', False)
                    result = config.set_playback_preferences(body, migrate=migrate)
                elif self.path == '/feeds/topics':
                    name = body.get('name')
                    if not isinstance(name, str):
                        raise ValueError('Choose a feed.')
                    result = {'name': name, 'topics': config.set_feed_topic_words(name, body.get('topics'))}
                else:
                    result = config.remove_feed_account(body.get('name'), body.get('username'))
                return self._send(200, json.dumps(result).encode(), 'application/json')
            except KeyError:
                return self._send(404, b'{"error":"Feed not found"}', 'application/json')
            except (ValueError, TypeError) as error:
                return self._send(400, json.dumps({'error': str(error)}).encode(), 'application/json')
            except Exception as error:
                return self._fail(error, as_json=True, context='Saving settings')
        if self.path in ('/feeds/import/preview', '/feeds/import'):
            return self._import_feeds()
        if self.path in ('/following/preview', '/following/import'):
            return self._following()
        if self.path.startswith("/comments/"):
            split = urlsplit(self.path)
            code = split.path.removeprefix("/comments/")
            if not re.fullmatch(r"[A-Za-z0-9_-]{5,30}", code):
                return self._send(400, b"invalid post code", "text/plain")
            # The panel pages through comments as it scrolls; the cursor is
            # Instagram's opaque JSON blob, so only bound its size.
            after = (parse_qs(split.query).get("after") or [None])[0]
            if after is not None and not 1 <= len(after) <= 2000:
                return self._send(400, b"invalid comment cursor", "text/plain")
            try:
                result = instagram.post_comments(code, after=after)
                return self._send(200, json.dumps(result).encode(), "application/json")
            except Exception as error:
                return self._fail(error, as_json=True, context="Loading comments")
        if self.path in ('/reels/view', '/reels/release', '/reels/renew'):
            try:
                length = int(self.headers.get('Content-Length') or 0)
                if not 0 < length <= 65536:
                    raise ValueError('Invalid request size')
                body = json.loads(self.rfile.read(length))
                client = body.get('client') if isinstance(body, dict) else None
                if not isinstance(client, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', client):
                    raise ValueError('Invalid client')
                if self.path == '/reels/view':
                    code, token, seconds = body.get('code'), body.get('reservation'), body.get('seconds')
                    if (not isinstance(code, str) or not POST_CODE.fullmatch(code) or
                            not isinstance(token, str) or not re.fullmatch(r'[a-f0-9]{32}', token) or
                            type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 3600):
                        raise ValueError('Invalid playback')
                    owner = history.record_playback(code, token, client, seconds)
                    if owner is None:
                        return self._send(409, b'{"accepted":false}', 'application/json')
                    recommendations.record_view(code, owner, token)
                    try:
                        recommendations.log_watch(token, code, owner, seconds)
                    except Exception:
                        # Stats are a convenience; never fail playback over them.
                        log.exception('Could not add %s to the watch log', code)
                else:
                    tokens = body.get('tokens')
                    if tokens is not None and (not isinstance(tokens, list) or len(tokens) > 1000 or any(
                            not isinstance(t, str) or not re.fullmatch(r'[a-f0-9]{32}', t) for t in tokens)):
                        raise ValueError('Invalid reservations')
                    if self.path == '/reels/release':
                        history.release(client, tokens)
                    else:
                        if tokens is None:
                            raise ValueError('Reservations required')
                        history.renew(client, tokens)
                return self._send(200, b'{"accepted":true}', 'application/json')
            except (ValueError, TypeError) as error:
                return self._send(400, json.dumps({'error':str(error)}).encode(), 'application/json')
            except Exception as error:
                return self._fail(error, as_json=True, context='Recording viewing activity')
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
        if self.path == "/saved-reels":
            return self._set_reel_saved()
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
        if self.path in ("/feeds/delete", "/feeds/restore"):
            try:
                length = int(self.headers.get('Content-Length') or 0)
                if not 0 < length <= 1024:
                    raise ValueError('Invalid request size')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('Invalid request')
                if self.path == '/feeds/delete':
                    name = body.get('name')
                    if not isinstance(name, str) or not FEED_RE.fullmatch(name):
                        raise ValueError('Invalid feed')
                    result = {'name':name, 'deleted':True, 'deletion_id':config.delete_feed(name)}
                else:
                    deletion_id = body.get('deletion_id')
                    if type(deletion_id) is not int:
                        raise ValueError('Invalid deletion')
                    result = {'name':config.restore_feed(deletion_id), 'restored':True}
                return self._send(200, json.dumps(result).encode(), 'application/json')
            except KeyError:
                return self._send(404, b'{"error":"Feed not found"}', 'application/json')
            except (ValueError, TypeError) as error:
                return self._send(400, json.dumps({'error':str(error)}).encode(), 'application/json')
            except Exception as error:
                return self._fail(error, as_json=True, context='Changing a feed')
        if self.path == "/feeds":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if not 0 < length <= 8192:
                    raise ValueError("Request is too large")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict) or not isinstance(body.get('name'), str):
                    raise ValueError("Enter a feed name")
                name = re.sub(r"\s+", "_", body['name'].strip().lower())
                if not FEED_RE.fullmatch(name):
                    raise ValueError("Use 1–20 letters, numbers, spaces, underscores or hyphens")
                create = body.get('create', False)
                if not isinstance(create, bool) or not isinstance(body.get('accounts', ''), str):
                    raise ValueError("Invalid feed request")
                accounts = []
                for value in re.split(r"[\s,]+", body.get('accounts', '').strip()):
                    if not value:
                        continue
                    if value.startswith(('instagram.com/', 'www.instagram.com/')):
                        value = 'https://' + value
                    if '://' in value:
                        url = urlsplit(value)
                        if url.scheme not in ('http', 'https') or url.hostname not in ('instagram.com', 'www.instagram.com'):
                            raise ValueError("Use Instagram profile links or @handles")
                        value = url.path.strip('/')
                        if '/' in value or value in ('p', 'reel', 'reels', 'explore'):
                            raise ValueError("Use a profile link, not a post link")
                    value = value.lstrip('@').lower()
                    if not USER_RE.fullmatch(value):
                        raise ValueError("Invalid Instagram handle: " + value[:40])
                    if value not in accounts:
                        accounts.append(value)
                if len(accounts) > 100:
                    raise ValueError("Add at most 100 accounts at once")
                if not create and not accounts:
                    raise ValueError("Enter at least one account")
                allowed = []
                with edit_feeds(unblock=allowed) as feeds:
                    if create and name in feeds:
                        return self._send(409, b'{"error":"That feed already exists"}', 'application/json')
                    if not create and name not in feeds:
                        raise ValueError("Choose an existing feed")
                    existing = accounts_from_feed(feeds.get(name))
                    feeds[name] = list(dict.fromkeys(existing + accounts))
                    allowed.extend(accounts)
                return self._send(200, json.dumps({'name': name, 'created': create,
                    'accounts': accounts}).encode(), 'application/json')
            except (ValueError, TypeError) as error:
                return self._send(400, json.dumps({'error': str(error)}).encode(), 'application/json')
            except Exception as error:
                return self._fail(error, as_json=True, context="Saving a feed")
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
        if self.path == "/add":  # explicitly adding an account allows its reels again
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                username = str(body.get("username", "")).lstrip("@").lower()
                if not USER_RE.fullmatch(username):
                    return self._send(400, b"bad username", "text/plain")
                allowed = []
                with edit_feeds(unblock=allowed) as feeds:
                    destination = str(body.get("feed", "")).lower()
                    if not FEED_RE.fullmatch(destination) or destination not in feeds:
                        return self._send(400, b"choose a valid feed", "text/plain")
                    cur = accounts_from_feed(feeds.get(destination))
                    allowed.append(username)
                    duplicate = username in cur
                    if not duplicate:
                        feeds[destination] = cur + [username]
                result = {"ok": True, "duplicate": True} if duplicate else {"ok": True, "added": username}
                return self._send(200, json.dumps(result).encode(), "application/json")
            except (ValueError, TypeError, AttributeError):
                return self._send(400, b"invalid request", "text/plain")
            except Exception as error:
                return self._fail(error, as_json=True, context="Adding an account")
        creator_match = re.fullmatch(r"/creator/([A-Za-z0-9._]{1,30})/more", self.path)
        m = re.fullmatch(r"/u/([A-Za-z0-9._]+)/(more|media)", self.path)
        rm = re.fullmatch(r"/(reels|all)/(more|media)", self.path)
        sound_match = re.fullmatch(r"/sound/(\d{10,32})/more", self.path)
        username = action = None
        if sound_match:
            try:
                data = sound_more(sound_match.group(1))
                return self._send(200, json.dumps(data).encode(), "application/json")
            except Exception as error:
                return self._fail(error, as_json=True, context="Loading more reels on this sound")
        elif m and USER_RE.match(m.group(1)):
            username, action = m.group(1).lower(), m.group(2)
        elif rm or creator_match:
            username, action = MIX, rm.group(2) if rm else "more"
        elif self.path == "/saved-reels/media" or re.fullmatch(r"/sound/\d{10,32}/media", self.path):
            action = "media"  # video lookups from the Saved reels and sound pages
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
                    client = body.get('client', 'legacy-client')
                    if not isinstance(client, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', client):
                        return self._send(400, b'invalid client', 'text/plain')
                    releases = body.get('release', [])
                    if not isinstance(releases, list) or len(releases) > 1000 or any(
                            not isinstance(t, str) or not re.fullmatch(r'[a-f0-9]{32}', t) for t in releases):
                        return self._send(400, b'invalid reservations', 'text/plain')
                    exclude_codes = body.get('exclude_codes', [])
                    if not isinstance(exclude_codes, list) or len(exclude_codes) > 1000 or any(
                            not isinstance(code, str) or not POST_CODE.fullmatch(code) for code in exclude_codes):
                        return self._send(400, b'invalid excluded reels', 'text/plain')
                    if releases:
                        history.release(client, releases)
                    if creator_match:
                        data = creator_more(creator_match.group(1).lower(), client=client,
                                            exclude_codes=exclude_codes, excluded={u.lower() for u in excluded})
                    else:
                        data = pool_more(feed_pool(MIX), excluded={u.lower() for u in excluded}, client=client, exclude_codes=exclude_codes)
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
        except (BrokenPipeError, ConnectionResetError):
            # The page stopped waiting (closed, reloaded, or changed feeds).
            log.debug("Client went away before %s finished", self.path)
        except (ValueError, TypeError, AttributeError) as error:
            log.info("Bad request to %s: %s", self.path, error)
            return self._send(400, json.dumps({"error": "invalid request"}).encode(), "application/json")
        except Exception as error:
            return self._fail(error, as_json=True)


def make_server(host: str, port: int, access=None) -> ThreadingHTTPServer:
    # A real server may fetch topic pages in the background; tests that build
    # a Handler directly stay offline.
    TOPICS.enabled = True
    SUGGESTIONS.enabled = True
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
    import webbrowser
    # Respect BROWSER and use the platform's default launcher on macOS/Windows.
    if os.environ.get("BROWSER") or sys.platform != "linux":
        try:
            if webbrowser.open(url):
                return True
        except webbrowser.Error:
            pass
        if sys.platform != "linux":
            return False
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
