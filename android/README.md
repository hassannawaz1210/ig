# Android preview

This APK runs the existing Python app on the phone and displays its player in
Android WebView. It does not connect to a desktop server. Feed databases live in
the app's private files directory; clearing app data or uninstalling deletes them.

The first build targets **64-bit ARM phones, Android 7.0+**. Python 3.13 and the
official Android `curl-cffi` 0.17.0 wheel preserve the existing logged-out request
implementation. Upstream currently ships no Android x86_64 wheel, so the same
APK needs ARM translation on an x86_64 emulator. CI uses a Google APIs image
with that support; a plain x86_64 image will not work.

## Build

The reproducible build runs on Linux with JDK 17, Android SDK 35 (including
command-line tools), Python 3.13, and Gradle 8.11.1. Set `ANDROID_HOME` to your SDK.
The native dependency build also needs `git`, `patch`, `patchelf`, `unzip`, and
`wget`. It downloads the required NDK through the SDK tools.

CFFI 2.1.1 is built from source because the available prebuilt Android CFFI
package is too old for curl-cffi. No dependency version checks are bypassed:

```sh
cd android
bash ci/build-cffi.sh
./gradlew :app:assembleDebug
```

The APK is `app/build/outputs/apk/debug/app-debug.apk`. To install it on a connected
phone with USB debugging enabled:

```sh
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

The `android` source set includes `../../src`, so changes to the shared app do
not need copying. Chaquopy extracts the `ig` package and its HTML/CSS/JS assets.
No desktop databases, feeds, virtual environment, or caches enter the APK.

CI passes the saved private preview key explicitly through `IG_ANDROID_KEYSTORE`
and verifies the APK certificate before publishing. Successive published APKs
can then be installed as updates while keeping feeds. Local builds use your own Android debug
key. Keep using the same signing key when building updates.

## Updates

Install the [latest APK](https://github.com/hassannawaz1210/ig/releases/download/android-updates/ig-android.apk)
once. The app checks for updates on launch at most daily, or immediately through
**Settings → Check for updates**. Updates download from this repository and open
Android's installer after checking the file hash, package, version, and signing
certificate. Android may ask you to allow installations from ig. Installing over
the existing app keeps its local database; do not uninstall first.

The Android workflow publishes only after the emulator and Python/browser checks
pass. Each build gets its own release asset. The `android-updates` release holds
the latest download and `update.json`, which points to that version's asset.
CI assigns increasing version codes automatically. Local builds can override
`IG_ANDROID_VERSION_CODE` and `IG_ANDROID_VERSION_NAME`; a differently signed
local build cannot update an official preview.

## Older preview migration

Builds up to `0.5.0-preview.31.1` were accidentally signed with different temporary
build-runner keys. Android cannot update those installations with the corrected
stable-key APK. The updater must not bypass that protection.

1. In the old app, open **Settings → Export backup**. Save the JSON outside the
   app and confirm it is present in your Files app.
2. Download the latest APK. Keep your backup before removing the old app.
3. Uninstall the old app, install the new APK, then choose **Settings → Import backup**.
4. Confirm your feeds, saved reels, blocks, and settings were restored.

The JSON backup does not include viewing history or learned recommendations.
For a full migration, an ADB backup of the debuggable app's local databases can
preserve those too. Do not remove the old app until you have a verified backup.
After this one-time migration, future releases use the stable key and install
in place through the app updater.

## Runtime

The shell starts Python on a background thread, starts one authenticated
loopback server, then opens it inside WebView. A process-local HttpOnly cookie
protects local routes; it is never put in an external URL or JavaScript bridge.
External links open in the user's browser. Android's Share menu accepts Instagram
reel links. Back closes the current sheet/dialog before navigating back.

The feed menu supports importing Instagram's following export (ZIP, JSON, or
HTML). **Export backup** saves feeds, saved reels, permanent blocks, and playback
settings through Android's system file picker; **Import backup** merges feeds
and saves and restores the exported settings on Android or desktop. Feeds and
permanent blocks are included, while history and saved reels are not. Existing
feeds are kept when importing. No storage permission is needed.

Closing or backgrounding the activity pauses video playback. Android may kill
its process; reopening starts the backend again using the same stored feeds.
Downloads use Android DownloadManager and are saved to the app's external-files
Downloads directory, avoiding broad storage permissions. Removing the app also
removes those files.

This is a feasibility build, not feature parity: Android initially uses the
progressive video fallback, and automatic song recognition is unavailable until
its native dependencies are ported. See the main usage guide for shared features.
Live Instagram requests remain subject to its logged-out limits.

See [device checks](ci/README.md) for emulator and physical-phone tests.
