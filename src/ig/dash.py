"""Best-quality reel playback from Instagram's DASH manifests.

Logged-out post pages serve a single progressive rendition (720p h264), but
the same payload also carries a DASH manifest whose top representation matches
the original upload resolution (typically 1080x1920 VP9). Every representation
is one complete fMP4 track file behind its BaseURL, so the best video and audio
tracks can simply be downloaded and muxed locally with the bundled ffmpeg.
Nothing here is sent to Instagram beyond the two track downloads."""
import logging
import subprocess
import threading
from collections import OrderedDict
from pathlib import Path
from xml.etree import ElementTree

from . import config
from .media import fetch_video
from curl_cffi import requests as cr

log = logging.getLogger(__name__)

MPD_NS = "{urn:mpeg:dash:schema:mpd:2011}"
CACHE_DIR = config.CACHE / "videos"
MAX_CACHED_FILES = 48       # assembled reels kept for replay; ~1 MB each
MAX_STASHED_MANIFESTS = 32  # manifests remembered between /media and /video
MUX_TIMEOUT = 60            # seconds the local ffmpeg mux may take

_assembly_locks: dict[str, threading.Lock] = {}
_stash_lock = threading.Lock()
_stash: OrderedDict[str, dict] = OrderedDict()


def _representations(mpd: str, content_type: str) -> list[dict]:
    """One entry per representation of a content type, BaseURL included."""
    try:
        root = ElementTree.fromstring(mpd)
    except ElementTree.ParseError:
        return []
    out = []
    for adapt in root.iter(f"{MPD_NS}AdaptationSet"):
        if adapt.get("contentType") != content_type:
            continue
        for rep in adapt.iter(f"{MPD_NS}Representation"):
            base = rep.find(f"{MPD_NS}BaseURL")
            url = (base.text or "").strip() if base is not None else ""
            if not url:
                continue
            out.append({"url": url,
                        "width": int(rep.get("width") or 0),
                        "height": int(rep.get("height") or 0),
                        "bandwidth": int(rep.get("bandwidth") or 0)})
    return out


def plan(mpd: str) -> dict | None:
    """The best video representation and the best audio one, or None.

    Resolution first, then bandwidth; the audio representation with the most
    bandwidth wins. A manifest without a usable video track is not a plan."""
    videos = [v for v in _representations(mpd, "video") if v["width"] and v["height"]]
    audios = _representations(mpd, "audio")
    if not videos:
        return None
    return {"video": max(videos, key=lambda v: (v["width"] * v["height"], v["bandwidth"])),
            "audio": max(audios, key=lambda a: a["bandwidth"]) if audios else None}


def _target(code: str) -> Path:
    return CACHE_DIR / f"{code}.mp4"


def cached(code: str) -> Path | None:
    """The already-assembled file for a reel, if it exists."""
    path = _target(code)
    return path if path.is_file() else None


def cleanup(keep: int = MAX_CACHED_FILES) -> None:
    """Delete the oldest assembled reels beyond the keep limit."""
    try:
        files = sorted(CACHE_DIR.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in files[keep:]:
            path.unlink(missing_ok=True)
    except OSError:
        log.debug("Could not clean up assembled videos", exc_info=True)


def stash(code: str, mpd: str, fallback_url: str | None) -> None:
    """Remember a reel's manifest between the /media and /video requests."""
    with _stash_lock:
        _stash[code] = {"mpd": mpd, "fallback": fallback_url}
        while len(_stash) > MAX_STASHED_MANIFESTS:
            _stash.popitem(last=False)


def recall(code: str) -> dict | None:
    with _stash_lock:
        return _stash.get(code)


def _download_track(url: str, path: Path) -> None:
    with cr.Session(impersonate="chrome") as session, path.open("wb") as file:
        response = fetch_video(session, url)
        try:
            if response.status_code != 200:
                raise OSError(f"track download returned HTTP {response.status_code}")
            for chunk in response.iter_content():
                file.write(chunk)
        finally:
            response.close()
    if not path.stat().st_size:
        raise OSError("track download was empty")


def assemble(code: str, mpd: str) -> Path | None:
    """Build and cache the best-quality file for a reel, or None on failure.

    Failures are expected occasionally: manifests rotate, URLs expire, and the
    caller falls back to the progressive rendition. One assembly runs per reel
    even when several tabs request the same video at once."""
    if cached(code) is not None:
        return cached(code)
    chosen = plan(mpd)
    if chosen is None:
        return None
    lock = _assembly_locks.setdefault(code, threading.Lock())
    with lock:
        if cached(code) is not None:
            return cached(code)
        video_track = _target(code).with_suffix(".track0")
        audio_track = _target(code).with_suffix(".track1")
        result = _target(code).with_suffix(".part")
        try:
            from imageio_ffmpeg import get_ffmpeg_exe
            executable = get_ffmpeg_exe()
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            _download_track(chosen["video"]["url"], video_track)
            inputs = ["-i", str(video_track)]
            if chosen["audio"] is not None:
                _download_track(chosen["audio"]["url"], audio_track)
                inputs += ["-i", str(audio_track)]
            command = [executable, "-y", "-v", "error", *inputs,
                       "-c", "copy", "-movflags", "+faststart",
                       "-f", "mp4", str(result)]
            subprocess.run(command, check=True, capture_output=True, timeout=MUX_TIMEOUT)
            if not result.stat().st_size:
                raise OSError("muxed video was empty")
            result.replace(_target(code))
            cleanup()
            log.info("Assembled %s at %sx%s", code,
                     chosen["video"]["width"], chosen["video"]["height"])
            return _target(code)
        except (OSError, subprocess.SubprocessError, ValueError, RuntimeError, ImportError) as error:
            log.info("DASH assembly failed for %s: %s", code, error)
            for path in (video_track, audio_track, result):
                path.unlink(missing_ok=True)
            return None
        finally:
            video_track.unlink(missing_ok=True)
            audio_track.unlink(missing_ok=True)
