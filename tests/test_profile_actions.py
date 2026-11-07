import shutil
import subprocess
import unittest
from pathlib import Path

from ig import views


class ProfileActionsTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is needed for profile action checks')
    def test_actions_and_comment_lifecycle(self):
        subprocess.run(['node', str(Path(__file__).with_name('profile_actions.cjs')),
                        str(views.STATIC_DIR / 'profile.js')], check=True)
