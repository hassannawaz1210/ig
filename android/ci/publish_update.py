"""Publish tested APKs and a small, stable update manifest for installed apps."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

OUTPUT = Path('android/build/update')
APK_DIR = Path('android/app/build/outputs/apk/debug')


def gh(*args):
    return subprocess.check_output(['gh', *args], text=True)


def prepare(apk_dir=APK_DIR, output=OUTPUT):
    metadata = json.loads((apk_dir / 'output-metadata.json').read_text())
    entries = metadata['elements']
    if len(entries) != 1:
        raise ValueError('Expected one universal ARM64 APK')
    entry = entries[0]
    name = entry['outputFile']
    if Path(name).name != name or not name.endswith('.apk'):
        raise ValueError('Invalid APK filename')
    apk = apk_dir / name
    size = apk.stat().st_size
    version = entry['versionCode']
    if type(version) is not int or not 7 <= version <= 2100000000 or not 0 < size <= 100 * 1024 * 1024:
        raise ValueError('Invalid APK version or size')
    repo, tag = os.environ['GH_REPO'], os.environ['UPDATE_TAG']
    if not re.fullmatch(r'[\w.-]+/[\w.-]+', repo) or not re.fullmatch(r'android-build-\d+-\d+', tag):
        raise ValueError('Invalid release destination')
    manifest = dict(version_code=version, version_name=entry['versionName'], size=size,
                    sha256=hashlib.sha256(apk.read_bytes()).hexdigest(),
                    apk_url=f'https://github.com/{repo}/releases/download/{tag}/ig-android.apk')
    output.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(apk, output / 'ig-android.apk')
    (output / 'update.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def wait_for_checks(repo, commit):
    # Checks is a separate workflow. Never publish while it is missing, pending,
    # cancelled, or failed, even if the native emulator passed.
    for _ in range(60):
        data = json.loads(gh('api', f'repos/{repo}/actions/workflows/checks.yml/runs?head_sha={commit}&event=push&per_page=20'))
        runs = [r for r in data['workflow_runs'] if r['head_sha'] == commit and r['head_branch'] == 'android-standalone']
        if runs:
            run = max(runs, key=lambda r: r['id'])
            if run['status'] == 'completed':
                if run['conclusion'] != 'success':
                    raise RuntimeError('Python/browser checks did not pass: ' + str(run['conclusion']))
                return
        time.sleep(10)
    raise RuntimeError('Timed out waiting for Python/browser checks')


def publish():
    repo, commit = (os.environ[k] for k in ('GH_REPO', 'UPDATE_COMMIT'))
    if repo != 'hassannawaz1210/ig':
        raise ValueError('Publishing is only configured for the upstream repository')
    wait_for_checks(repo, commit)
    manifest = json.loads((OUTPUT / 'update.json').read_text())
    match = re.fullmatch(r'https://github\.com/' + re.escape(repo) + r'/releases/download/(android-build-\d+-\d+)/ig-android\.apk', manifest['apk_url'])
    if not match:
        raise ValueError('Invalid update asset URL')
    tag = match[1]  # Rerunning only this job reuses the original build artifact.
    apk = OUTPUT / 'ig-android.apk'
    if (apk.stat().st_size != manifest['size'] or
            hashlib.sha256(apk.read_bytes()).hexdigest() != manifest['sha256'] or
            manifest['apk_url'] != f'https://github.com/{repo}/releases/download/{tag}/ig-android.apk'):
        raise ValueError('APK does not match update metadata')
    # API errors fail closed. Only a successful listing can establish that the
    # rolling release does not exist; a network failure must not reset it.
    pages = json.loads(gh('api', '--paginate', '--slurp', f'repos/{repo}/releases?per_page=100'))
    releases = [release for page in pages for release in page]
    rolling = next((r for r in releases if r['tag_name'] == 'android-updates'), None)
    if rolling and any(a['name'] == 'update.json' for a in rolling['assets']):
        with tempfile.TemporaryDirectory() as directory:
            gh('release', 'download', 'android-updates', '--pattern', 'update.json', '--dir', directory)
            previous = json.loads((Path(directory) / 'update.json').read_text())
        if previous['version_code'] >= manifest['version_code']:
            print('A matching or newer update is already published')
            return
    notes = OUTPUT / 'release-notes.txt'
    notes.write_text('Tested Android preview. Install over the existing app to keep your feeds and settings.\n\n'
                     f'Version: {manifest["version_name"]}\nSource: {commit}\n')
    existing = next((r for r in releases if r['tag_name'] == tag), None)
    if not existing:
        gh('release', 'create', tag, str(apk), '--target', commit, '--title', 'Android ' + manifest['version_name'],
           '--notes-file', str(notes), '--prerelease')
    elif not any(a['name'] == apk.name for a in existing['assets']):
        gh('release', 'upload', tag, str(apk))
    if not rolling:
        gh('release', 'create', 'android-updates', '--target', commit, '--title', 'Android updates',
           '--notes', 'Latest tested Android preview. Install ig-android.apk once; future updates appear inside the app.', '--prerelease')
    # The manifest points at the immutable version asset. Upload it last so a
    # client never sees an update before its complete APK is available.
    gh('release', 'upload', 'android-updates', str(apk), '--clobber')
    gh('release', 'upload', 'android-updates', str(OUTPUT / 'update.json'), '--clobber')
    print('Published ' + manifest['apk_url'])


if __name__ == '__main__':
    {'prepare': prepare, 'publish': publish}[sys.argv[1]]()
