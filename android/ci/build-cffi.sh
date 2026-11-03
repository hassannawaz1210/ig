#!/usr/bin/env bash
# Build genuine CFFI 2.1.1 for the same Android/Python ABI as curl-cffi.
set -euo pipefail
android_dir=$(cd "$(dirname "$0")/.." && pwd)
recipe="$android_dir/ci/recipes/cffi"
wheel_dir="$android_dir/wheels"
build_root=$(mktemp -d "${TMPDIR:-/tmp}/ig-cffi-build.XXXXXX")
chaquopy_commit=71a373b35c21ce4c9ed9934174ae9d4e11c2a2e0
target_version=3.13.9-0
libffi_wheel=chaquopy_libffi-3.3-3-py3-none-android_24_arm64_v8a.whl
: "${ANDROID_HOME:?Set ANDROID_HOME to an Android SDK with command-line tools}"
for tool in python3.13 git patch patchelf unzip wget; do
  command -v "$tool" >/dev/null || { echo "Missing build tool: $tool" >&2; exit 1; }
done
mkdir -p "$wheel_dir"
git init -q "$build_root/chaquopy"
git -C "$build_root/chaquopy" remote add origin https://github.com/chaquo/chaquopy.git
git -C "$build_root/chaquopy" fetch --depth 1 origin "$chaquopy_commit"
git -C "$build_root/chaquopy" checkout -q --detach FETCH_HEAD
# The upstream compiler wrapper only allows headers/libraries under its own
# checkout. Keep the recipe there so native libffi/Python include paths survive.
cp "$recipe/meta.yaml" "$build_root/chaquopy/server/pypi/packages/cffi/meta.yaml"
python3.13 -m venv "$build_root/tools"
"$build_root/tools/bin/python" -m pip install -r "$build_root/chaquopy/server/pypi/requirements.txt"
cd "$build_root/chaquopy"
bash target/download-target.sh "maven/com/chaquo/python/target/$target_version"
mkdir -p server/pypi/dist/chaquopy-libffi
wget -q "https://chaquo.com/pypi-13.1/chaquopy-libffi/$libffi_wheel" \
  -O "server/pypi/dist/chaquopy-libffi/$libffi_wheel"
cd server/pypi
"$build_root/tools/bin/python" build-wheel.py --python 3.13 --abi arm64-v8a --api-level 24 cffi
cp dist/cffi/cffi-2.1.1-*-cp313-cp313-android_24_arm64_v8a.whl "$wheel_dir/"
cp "dist/chaquopy-libffi/$libffi_wheel" "$wheel_dir/"
"$build_root/tools/bin/python" - "$wheel_dir" <<'PY'
import pathlib, sys, zipfile
wheels = list(pathlib.Path(sys.argv[1]).glob('cffi-2.1.1-*-cp313-cp313-android_24_arm64_v8a.whl'))
assert len(wheels) == 1, wheels
with zipfile.ZipFile(wheels[0]) as wheel:
    assert any(name.startswith('_cffi_backend') and name.endswith('.so') for name in wheel.namelist())
    metadata = wheel.read('cffi-2.1.1.dist-info/METADATA').decode()
    assert 'Version: 2.1.1\n' in metadata
print('Built Android CFFI wheel:', wheels[0].name)
PY
