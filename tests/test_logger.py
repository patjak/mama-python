import os
import pathlib
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from mama import config
from mama.logger import Log


class TestLoggerPermissions(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.orig_mama_path = getattr(config, "MAMA_PATH", "/usr/share/mama")
        config.MAMA_PATH = self.tmp_dir.name

    def tearDown(self):
        Log.set_file(None)
        config.MAMA_PATH = self.orig_mama_path
        self.tmp_dir.cleanup()

    def test_log_file_created_with_group_write_permission(self):
        # Create a log file and verify group write bit is set
        Log.set_file("test-mach")
        log_file = pathlib.Path(self.tmp_dir.name) / "log" / "test-mach"
        self.assertTrue(log_file.exists())

        mode = log_file.stat().st_mode
        self.assertTrue(bool(mode & stat.S_IWGRP), "Log file should have group write permission")
        self.assertTrue(bool(mode & stat.S_IRGRP), "Log file should have group read permission")

    def test_log_file_existing_chmod(self):
        log_file = pathlib.Path(self.tmp_dir.name) / "log" / "existing-mach"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        # Create initially with 0600 (no group permissions)
        log_file.write_text("initial log\n")
        os.chmod(str(log_file), 0o600)

        # Calling set_file should add group read/write
        Log.set_file("existing-mach")
        mode = log_file.stat().st_mode
        self.assertTrue(bool(mode & stat.S_IWGRP), "Existing log file should have group write permission added")
        self.assertTrue(bool(mode & stat.S_IRGRP), "Existing log file should have group read permission added")


if __name__ == "__main__":
    unittest.main()
