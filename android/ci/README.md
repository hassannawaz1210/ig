# Android checks

The `Android APK` workflow builds the ARM64 app and its AndroidX test APK. It
uploads both before trying the emulator, so a device can still be tested if the
hosted emulator fails to start.

API 35's Google APIs x86_64 image can run ARM64 app code through Android's native
translation. The smoke script checks the emulator's ABI list rather than
substituting an x86 networking implementation or skipping the Python import.

The instrumented test checks the packaged player in a real WebView, calls the
Python backend checks, and recreates the Activity. Two further instrumentation
invocations write a feed, stop the app process, and check the feed in a fresh
process. Those checks run without Instagram requests. They do not prove that
Instagram will accept a phone's network requests or that a real reel plays.

For a disposable test installation on an attached ARM64 device, with Android
SDK `adb`, Java 17, Python 3.13 and Gradle 8.11.1 available:

```sh
cd android
gradle assembleDebug assembleDebugAndroidTest
bash ci/emulator-smoke.sh
```

Use one connected device at a time. The script creates and removes feeds named
`android_smoke` and `android_ci`; don't run it against your everyday app profile.
Results, screenshots, and logcat are written to `android/build/smoke/`.
