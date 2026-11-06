import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('verify_signing', Path(__file__).parents[1] / 'android/ci/verify_signing.py')
signing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(signing)


class AndroidSigningTests(unittest.TestCase):
    def test_only_valid_apk_with_pinned_certificate_is_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            tool = Path(folder) / 'build-tools/35.0.0/apksigner'
            tool.parent.mkdir(parents=True)
            tool.touch()
            with patch.dict(os.environ, ANDROID_HOME=folder):
                correct = 'Signer #1 certificate SHA-256 digest: ' + signing.EXPECTED_CERTIFICATE + '\n'
                with patch.object(signing.subprocess, 'check_output', return_value=correct) as command:
                    signing.verify('app.apk')
                    self.assertIn('verify', command.call_args.args[0])
                for output in [correct.replace('Signer #1', 'Signer (minSdkVersion=24, maxSdkVersion=32)'), correct+correct]:
                    with patch.object(signing.subprocess, 'check_output', return_value=output):
                        signing.verify('app.apk')
                for output in ['', correct.replace(signing.EXPECTED_CERTIFICATE, 'a'*64), correct+correct.replace('#1','#2').replace(signing.EXPECTED_CERTIFICATE,'b'*64)]:
                    with patch.object(signing.subprocess, 'check_output', return_value=output), self.assertRaises(RuntimeError):
                        signing.verify('app.apk')
                with patch.object(signing.subprocess, 'check_output', side_effect=subprocess.CalledProcessError(1, 'apksigner')), self.assertRaises(subprocess.CalledProcessError):
                    signing.verify('app.apk')
