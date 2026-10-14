import subprocess
import sys
import tempfile
import unittest
from plex_presence.instance import InstanceLock


class InstanceTests(unittest.TestCase):
    def test_other_process_cannot_use_same_directory_then_can_after_release(self):
        with tempfile.TemporaryDirectory() as directory:
            command = [sys.executable, "-c", "from plex_presence.instance import InstanceLock; import sys; lock = InstanceLock(sys.argv[1]); lock.close()", directory]
            lock = InstanceLock(directory)
            try:
                self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            finally:
                lock.close()
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
