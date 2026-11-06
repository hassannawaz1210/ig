"""Release ordering and integrity checks; never calls the real GitHub CLI."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('publish_update', Path(__file__).parents[1] / 'android/ci/publish_update.py')
updates = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updates)


class AndroidUpdatesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.apk_dir = self.root / 'apk'
        self.apk_dir.mkdir()
        self.output = self.root / 'update'
        (self.apk_dir / 'app-debug.apk').write_bytes(b'apk fixture')
        self.metadata = {'elements': [{'outputFile': 'app-debug.apk', 'versionCode': 1201, 'versionName': 'preview.2'}]}
        (self.apk_dir / 'output-metadata.json').write_text(json.dumps(self.metadata))
        self.env = patch.dict(os.environ, GH_REPO='hassannawaz1210/ig', UPDATE_COMMIT='abc123', UPDATE_TAG='android-build-20-1')
        self.env.start()
        self.addCleanup(self.env.stop)
        self.manifest = updates.prepare(self.apk_dir, self.output)
        p = patch.object(updates, 'OUTPUT', self.output)
        p.start()
        self.addCleanup(p.stop)

    def test_manifest_matches_apk(self):
        self.assertEqual(self.manifest['size'], 11)
        self.assertEqual(self.manifest['version_code'], 1201)
        self.assertEqual(self.manifest['apk_url'], 'https://github.com/hassannawaz1210/ig/releases/download/android-build-20-1/ig-android.apk')
        self.assertEqual((self.output / 'ig-android.apk').read_bytes(), b'apk fixture')

    def test_invalid_filename_rejected(self):
        self.metadata['elements'][0]['outputFile'] = '../app.apk'
        (self.apk_dir / 'output-metadata.json').write_text(json.dumps(self.metadata))
        with self.assertRaises(ValueError):
            updates.prepare(self.apk_dir, self.output)

    def test_failed_checks_never_publish(self):
        run = dict(id=1, head_sha='abc123', head_branch='android-standalone', status='completed', conclusion='failure')
        with patch.object(updates, 'gh', return_value=json.dumps({'workflow_runs': [run]})) as gh:
            with self.assertRaises(RuntimeError):
                updates.publish()
            self.assertEqual(gh.call_count, 1)

    def test_tampered_apk_never_publish(self):
        (self.output / 'ig-android.apk').write_bytes(b'tampered')
        with patch.object(updates, 'wait_for_checks'), patch.object(updates, 'gh') as gh:
            with self.assertRaises(ValueError):
                updates.publish()
            gh.assert_not_called()

    def test_manifest_is_uploaded_last_and_retry_uses_original_tag(self):
        with patch.dict(os.environ, UPDATE_TAG='android-build-20-2'), patch.object(updates, 'wait_for_checks'), patch.object(updates, 'gh', return_value='[[]]') as gh:
            updates.publish()
        calls = [c.args for c in gh.call_args_list]
        self.assertIn(('release', 'create', 'android-build-20-1'), [c[:3] for c in calls])
        self.assertEqual(calls[-1], ('release', 'upload', 'android-updates', str(self.output / 'update.json'), '--clobber'))

    def test_never_roll_back_published_version(self):
        def fake_gh(*args):
            if args[0] == 'api':
                return json.dumps([[{'tag_name': 'android-updates', 'assets': [{'name': 'update.json'}]}]])
            if args[:2] == ('release', 'download'):
                (Path(args[-1]) / 'update.json').write_text(json.dumps({'version_code': 1301}))
                return ''
            self.fail('Attempted to replace a newer update')
        with patch.object(updates, 'wait_for_checks'), patch.object(updates, 'gh', side_effect=fake_gh):
            updates.publish()
