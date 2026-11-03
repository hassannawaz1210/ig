"""Automatic song identification using Shazam's fingerprint service."""
import asyncio
import logging
import tempfile
import subprocess
import threading
from pathlib import Path
from urllib.parse import urlsplit
from curl_cffi import requests as cr
from . import config
from .reels import media_info
from .media import fetch_video
from .recognition_cache import cached_song, cache_song

log = logging.getLogger(__name__)

WORKERS = threading.BoundedSemaphore(2)
MAX_VIDEO_BYTES = 40 * 1024 * 1024


async def identify_file(path):
    from shazamio import Shazam
    from imageio_ffmpeg import get_ffmpeg_exe
    # Instagram uses AAC variants the fingerprint decoder cannot read directly.
    # Decode just 12 seconds to mono PCM locally before creating the fingerprint.
    audio = Path(path).with_suffix('.recognition.wav')
    try:
        subprocess.run([get_ffmpeg_exe(), '-nostdin', '-v', 'error', '-y',
                        '-i', str(path), '-t', '12', '-vn', '-ac', '1', '-ar', '16000',
                        '-c:a', 'pcm_s16le', str(audio)],
                       check=True, capture_output=True, timeout=20)
        async with Shazam() as shazam:
            return await asyncio.wait_for(shazam.recognize(audio), timeout=40)
    finally:
        audio.unlink(missing_ok=True)


def track_result(payload):
    track = payload.get('track') or {}
    if not track.get('title'):
        return {'status': 'no_match'}
    url = track.get('url', '')
    if urlsplit(url).scheme != 'https':
        url = ''
    return {'status': 'matched', 'title': track['title'],
            'artist': track.get('subtitle', ''), 'url': url}


def recognize_song(code):
    if config.ANDROID:
        return {'status': 'unavailable'}
    cached = cached_song(code)
    if cached is not None:
        return cached
    if not WORKERS.acquire(blocking=False):
        return {'status': 'busy'}
    try:
        # Another request may have finished while we checked the cache.
        cached = cached_song(code)
        if cached is not None:
            return cached
        url = media_info(code).get('video')
        parsed = urlsplit(url or '')
        host = parsed.hostname or ''
        if parsed.scheme != 'https' or not any(
                host == domain or host.endswith('.' + domain)
                for domain in ('cdninstagram.com', 'fbcdn.net')):
            return {'status': 'unavailable'}
        with tempfile.TemporaryDirectory(prefix='ig-song-') as directory:
            path = Path(directory) / 'reel.mp4'
            with cr.Session(impersonate='chrome') as session:
                response = fetch_video(session, url)
                try:
                    if response.status_code != 200:
                        return {'status': 'unavailable'}
                    size = 0
                    with path.open('wb') as file:
                        for chunk in response.iter_content():
                            size += len(chunk)
                            if size > MAX_VIDEO_BYTES:
                                return {'status': 'unavailable'}
                            file.write(chunk)
                finally:
                    response.close()
            if not size:
                return {'status': 'unavailable'}
            result = track_result(asyncio.run(identify_file(path)))
        cache_song(code, result)
        return result
    except (Exception, SystemExit) as error:
        # Recognition failures must never interrupt playback or become a false no-match.
        log.warning('Song recognition failed for %s (%s)', code, type(error).__name__)
        return {'status': 'unavailable'}
    finally:
        WORKERS.release()
