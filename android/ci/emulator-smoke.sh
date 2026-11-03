#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p build/smoke
app_id=io.github.hassannawaz1210.ig
runner="$app_id.test/androidx.test.runner.AndroidJUnitRunner"
test_class="$app_id.StandaloneSmokeTest"
collect_logs() {
  adb logcat -d > build/smoke/logcat.txt || true
  adb exec-out screencap -p > build/smoke/screen.png || true
}
trap collect_logs EXIT
adb logcat -c
adb shell getprop ro.product.cpu.abilist | tee build/smoke/device-abis.txt
adb shell getprop ro.dalvik.vm.native.bridge | tee build/smoke/native-bridge.txt
# Google APIs system images support ARM apps through native translation. Do
# not substitute an x86 build or silently skip the real native Python import.
grep -q 'arm64-v8a' build/smoke/device-abis.txt
gradle --no-daemon --stacktrace connectedDebugAndroidTest

# Gradle removes test packages after connected tests. Install both APKs again
# for the independent process-restart checks below.
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb install -r app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk

# Separate instrumentation invocations give Python a genuinely fresh process.
# No Instagram calls are required for these local-storage checks.
run_persistence_check() {
  local mode="$1"
  adb shell am instrument -w -r -e class "$test_class#backendAndPlayer" -e persistence "$mode" "$runner" \
    | tee "build/smoke/$mode.txt"
  grep -q 'OK (1 test)' "build/smoke/$mode.txt"
  if grep -qE 'FAILURES!!!|INSTRUMENTATION_FAILED|Process crashed' "build/smoke/$mode.txt"; then
    return 1
  fi
}
run_persistence_check seed
adb shell am force-stop "$app_id"
run_persistence_check verify
adb shell am force-stop "$app_id"
adb shell am start -W -n "$app_id/.MainActivity"
# Preserve a screenshot of the actual app after the persistence checks.
sleep 3
