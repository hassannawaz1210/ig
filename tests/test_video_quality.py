import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ig import config, dash
from ig.server import Handler


def mpd(video_reps, audio_reps=0):
    """A manifest shaped like Instagram's, with per-representation BaseURLs."""
    def rep(attrs, url):
        base = f"<BaseURL>{url}</BaseURL>" if url else ""
        return f"<Representation {attrs}>{base}</Representation>"
    videos = "".join(rep(f'id="v{n}" bandwidth="{200 + n}" width="{w}" '
                         f'height="{h}" codecs="vp09"', f"https://cdn.fbcdn.net/v{n}.mp4")
                   for n, (w, h) in enumerate(video_reps))
    audios = "".join(rep(f'id="a{n}" bandwidth="{50 + n}" codecs="mp4a.40.5"',
                         f"https://cdn.fbcdn.net/a{n}.mp4") for n in range(audio_reps))
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><Period>'
            f'<AdaptationSet contentType="video">{videos}</AdaptationSet>'
            f'<AdaptationSet contentType="audio">{audios}</AdaptationSet>'
            '</Period></MPD>')


class DashPlanTests(unittest.TestCase):
    def test_highest_resolution_wins_and_audio_is_max_bandwidth(self):
        plan = dash.plan(mpd([(720, 1280), (1080, 1920), (640, 1138)], audio_reps=2))
        self.assertEqual(plan["video"]["url"], "https://cdn.fbcdn.net/v1.mp4")
        self.assertEqual(plan["audio"]["url"], "https://cdn.fbcdn.net/a1.mp4")

    def test_no_usable_video_representations_is_no_plan(self):
        self.assertIsNone(dash.plan(mpd([(0, 0)])))
        self.assertIsNone(dash.plan("<not-xml"))
        self.assertIsNone(dash.plan(""))


class DashAssembleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher = patch.object(dash, 'CACHE_DIR', Path(self.directory.name) / 'videos')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_muxes_best_tracks_and_caches_the_result(self):
        def fake_download(url, path):
            path.write_bytes(b"track:" + url.rsplit("/", 1)[-1].encode())
        command = {}
        def fake_run(cmd, **kwargs):
            command["cmd"] = cmd
            output = Path(cmd[-1])
            output.write_bytes(b"muxed")
            return MagicMock(returncode=0)
        with patch.object(dash, '_download_track', side_effect=fake_download), \
                patch.object(dash.subprocess, 'run', side_effect=fake_run):
            result = dash.assemble("AbCdEfGhIj", mpd([(1080, 1920)], audio_reps=1))
        self.assertEqual(result.read_bytes(), b"muxed")
        self.assertIn("-i", command["cmd"])
        self.assertEqual(command["cmd"].count("-i"), 2)  # video and audio tracks
        self.assertEqual(dash.cached("AbCdEfGhIj"), result)

    def test_assembles_without_an_audio_track(self):
        def fake_download(url, path):
            path.write_bytes(b"track")
        def fake_run(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b"muxed")
            return MagicMock(returncode=0)
        with patch.object(dash, '_download_track', side_effect=fake_download), \
                patch.object(dash.subprocess, 'run', side_effect=fake_run):
            result = dash.assemble("AbCdEfGhIj", mpd([(1080, 1920)]))
        self.assertEqual(result.read_bytes(), b"muxed")

    def test_failure_returns_none_and_leaves_no_partial_files(self):
        with patch.object(dash, '_download_track', side_effect=OSError("expired")), \
                patch.object(dash.subprocess, 'run') as run:
            self.assertIsNone(dash.assemble("AbCdEfGhIj", mpd([(1080, 1920)], audio_reps=1)))
        run.assert_not_called()
        self.assertEqual(list(Path(self.directory.name).glob("**/*.mp4")), [])

    def test_second_assembly_of_the_same_reel_is_free(self):
        Path(self.directory.name, "videos").mkdir(parents=True)
        dash._target("AbCdEfGhIj").write_bytes(b"here")
        with patch.object(dash, '_download_track') as download:
            self.assertEqual(dash.assemble("AbCdEfGhIj", mpd([(1080, 1920)])),
                             dash._target("AbCdEfGhIj"))
        download.assert_not_called()

    def test_cleanup_bounds_the_cache(self):
        cache = Path(self.directory.name, "videos")
        cache.mkdir(parents=True)
        for n in range(dash.MAX_CACHED_FILES + 5):
            (cache / f"f{n}.mp4").write_bytes(b"x")
        dash.cleanup()
        self.assertEqual(len(list(cache.glob("*.mp4"))), dash.MAX_CACHED_FILES)


class MediaInfoSelectionTests(unittest.TestCase):
    def test_progressive_fallback_picks_largest_area_when_dimensions_exist(self):
        from ig import instagram
        media_page = {"require": [["ScheduledServerJS", "handle", None, {"__bbox": {"require": [
            ["RelayPrefetchedStreamCache", "next", [],
             {"xig_polaris_media": {"if_not_gated_logged_out": {
                 "pk": "1", "code": "AbCdEfGhIjK", "user": {"username": "creator"},
                 "video_versions": [
                     {"width": 1080, "height": 1920, "url": "https://cdn.fbcdn.net/big.mp4"},
                     {"width": 1440, "height": 700, "url": "https://cdn.fbcdn.net/wide.mp4"},
                     {"width": 720, "height": 1280, "url": "https://cdn.fbcdn.net/small.mp4"},
                 ],
                 "video_dash_manifest": "<MPD/>",
             }}},
             "done"]]}}, None]]}
        import json
        html = ('<html><script type="application/json" data-sjs>'
                f'{json.dumps(media_page, separators=(",", ":"))}</script></html>')
        with patch.object(instagram, '_post_page', return_value=html):
            data = instagram.media_info('AbCdEfGhIjK')
        self.assertEqual(data['video'], 'https://cdn.fbcdn.net/big.mp4')
        self.assertEqual(data['video_dash_manifest'], '<MPD/>')


class VideoStreamTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher = patch.object(dash, 'CACHE_DIR', Path(self.directory.name) / 'videos')
        patcher.start()
        self.addCleanup(patcher.stop)

    def handler(self, headers=None):
        handler = Handler.__new__(Handler)
        handler.wfile = io.BytesIO()
        handler.headers = headers or {}
        for name in ['send_response', 'send_header', 'end_headers', '_send']:
            setattr(handler, name, MagicMock())
        return handler

    def test_media_swaps_in_the_local_stream_and_hides_the_manifest(self):
        class InlineThread:
            def __init__(self, target, **kwargs):
                self.target = target
            def start(self):
                self.target()
        media = {'shortcode': 'AbCdEfGhIj', 'owner': 'creator',
                 'video': 'https://cdn.fbcdn.net/prog.mp4',
                 'video_dash_manifest': mpd([(1080, 1920)], audio_reps=1)}
        with patch('ig.server.media_info', return_value=media), \
                patch('ig.server.threading.Thread', InlineThread), \
                patch.object(dash, 'assemble', return_value=None) as assemble, \
                patch('ig.instagram.post_comments'):
            data = Handler.__new__(Handler)._media('AbCdEfGhIj')
        self.assertEqual(data['video'], '/video/AbCdEfGhIj')
        self.assertNotIn('video_dash_manifest', data)
        # The assembly was pre-warmed in the background.
        assemble.assert_called_once()
        self.assertEqual(dash.recall('AbCdEfGhIj')['fallback'],
                         'https://cdn.fbcdn.net/prog.mp4')

    def test_video_serves_cached_file_and_range_slices(self):
        cache = Path(self.directory.name, 'videos')
        cache.mkdir(parents=True)
        (cache / 'AbCdEfGhIj.mp4').write_bytes(b'0123456789')
        handler = self.handler()
        handler._video('AbCdEfGhIj')
        handler.send_response.assert_called_once_with(200)
        handler.send_header.assert_any_call('Content-Length', '10')
        self.assertEqual(handler.wfile.getvalue(), b'0123456789')
        # A range request returns exactly the requested slice.
        ranged = self.handler({'Range': 'bytes=2-5'})
        ranged._video('AbCdEfGhIj')
        ranged.send_response.assert_called_once_with(206)
        ranged.send_header.assert_any_call('Content-Range', 'bytes 2-5/10')
        self.assertEqual(ranged.wfile.getvalue(), b'2345')

    def test_video_falls_back_to_the_progressive_url(self):
        handler = self.handler()
        with patch.object(dash, 'cached', return_value=None), \
                patch.object(dash, 'assemble', return_value=None):
            dash.stash('AbCdEfGhIj', mpd([(1080, 1920)]), 'https://cdn.fbcdn.net/prog.mp4')
            handler._video('AbCdEfGhIj')
        handler.send_response.assert_called_once_with(302)
        handler.send_header.assert_any_call('Location', 'https://cdn.fbcdn.net/prog.mp4')
        self.assertEqual(handler.wfile.getvalue(), b'')

    def test_download_prefers_the_assembled_stream(self):
        cache = Path(self.directory.name, 'videos')
        cache.mkdir(parents=True)
        (cache / 'AbCdEfGhIj.mp4').write_bytes(b'bestquality')
        handler = self.handler()
        with patch.object(dash, 'cached', return_value=cache / 'AbCdEfGhIj.mp4'), \
                patch('ig.server.media_info') as media:
            handler._download('AbCdEfGhIj')
        media.assert_not_called()
        handler.send_header.assert_any_call('Content-Disposition',
                                            'attachment; filename="AbCdEfGhIj.mp4"')
        self.assertEqual(handler.wfile.getvalue(), b'bestquality')
