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

## Runtime

The shell starts Python on a background thread, starts one authenticated
loopback server, then opens it inside WebView. A process-local HttpOnly cookie
protects local routes; it is never put in an external URL or JavaScript bridge.
External links open in the user's browser. Android's Share menu accepts Instagram
reel links. Back closes the current sheet/dialog before navigating back.

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
