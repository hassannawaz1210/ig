"""Reject an APK signed with an ephemeral runner key before it can be published."""
import os
from pathlib import Path
import re
import subprocess
import sys

# Public certificate fingerprint of the backed-up preview key; never the key itself.
EXPECTED_CERTIFICATE = 'e0ae751b94e608392125cba3322259081dc994c5c38f7b31679960617b9666f5'


def verify(apk):
    sdk = Path(os.environ.get('ANDROID_HOME') or os.environ['ANDROID_SDK_ROOT'])
    signers = sorted(sdk.glob('build-tools/*/apksigner'))
    if not signers:
        raise RuntimeError('Android apksigner is unavailable')
    output = subprocess.check_output([str(signers[-1]), 'verify', '--print-certs', str(apk)], text=True)
    certificates = re.findall(r'certificate SHA-256 digest:\s*([0-9a-fA-F]{64})(?:\s|$)', output, re.MULTILINE)
    if {value.lower() for value in certificates} != {EXPECTED_CERTIFICATE}:
        raise RuntimeError('APK signing certificate differs from the saved preview key; refusing publication. Found: ' + repr(certificates) + '\n' + output)
    print('APK signature verified against the saved preview certificate')


if __name__ == '__main__':
    verify(Path(sys.argv[1]))
