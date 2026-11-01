import pathlib
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from mama.builder import chroot_mounts


class TestBuilderMounts(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_dir = pathlib.Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("mama.builder.Util.is_root", return_value=True)
    @patch("mama.builder.subprocess.run")
    def test_chroot_mounts_normal_lifecycle(self, mock_run, mock_is_root):
        mock_run.return_value = MagicMock(returncode=0)

        with chroot_mounts(self.root_dir):
            # Check mounts were executed
            mount_calls = [c for c in mock_run.call_args_list if c[0][0][0] == "mount"]
            self.assertEqual(len(mount_calls), 2)
            self.assertEqual(mount_calls[0][0][0], ["mount", "-t", "proc", "/proc", str(self.root_dir / "proc")])
            self.assertEqual(mount_calls[1][0][0], ["mount", "-t", "sysfs", "sysfs", str(self.root_dir / "sys")])

        # Check umounts were executed in reverse order on exit
        umount_calls = [c for c in mock_run.call_args_list if c[0][0][0] == "umount"]
        self.assertEqual(len(umount_calls), 2)
        self.assertEqual(umount_calls[0][0][0], ["umount", str(self.root_dir / "sys")])
        self.assertEqual(umount_calls[1][0][0], ["umount", str(self.root_dir / "proc")])

    @patch("mama.builder.Util.is_root", return_value=True)
    @patch("mama.builder.subprocess.run")
    def test_chroot_mounts_cleanup_on_exception(self, mock_run, mock_is_root):
        mock_run.return_value = MagicMock(returncode=0)

        with self.assertRaises(ValueError):
            with chroot_mounts(self.root_dir):
                raise ValueError("Build interrupted!")

        # Verify umounts still executed despite the exception
        umount_calls = [c for c in mock_run.call_args_list if c[0][0][0] == "umount"]
        self.assertEqual(len(umount_calls), 2)
        self.assertEqual(umount_calls[0][0][0], ["umount", str(self.root_dir / "sys")])
        self.assertEqual(umount_calls[1][0][0], ["umount", str(self.root_dir / "proc")])


if __name__ == "__main__":
    unittest.main()
