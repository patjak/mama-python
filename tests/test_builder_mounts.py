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

    def test_get_kiwi_proxy_args_disabled_by_default(self):
        from mama import config
        from mama.builder import get_kiwi_proxy_args

        orig = config.PACKAGE_PROXY_ENABLED
        try:
            config.PACKAGE_PROXY_ENABLED = False
            self.assertEqual(get_kiwi_proxy_args(), [])
        finally:
            config.PACKAGE_PROXY_ENABLED = orig

    @patch("mama.builder.is_proxy_available", return_value=True)
    def test_get_kiwi_proxy_args_enabled_and_reachable(self, mock_avail):
        from mama import config
        from mama.builder import get_kiwi_proxy_args

        orig = config.PACKAGE_PROXY_ENABLED
        try:
            config.PACKAGE_PROXY_ENABLED = True
            args = get_kiwi_proxy_args()
            self.assertEqual(len(args), 4)
            self.assertTrue(any("http_proxy=" in a for a in args))
        finally:
            config.PACKAGE_PROXY_ENABLED = orig

    @patch("mama.builder.is_proxy_available", return_value=False)
    def test_get_kiwi_proxy_args_enabled_but_unreachable_falls_back(self, mock_avail):
        from mama import config
        from mama.builder import get_kiwi_proxy_args

        orig = config.PACKAGE_PROXY_ENABLED
        try:
            config.PACKAGE_PROXY_ENABLED = True
            # When proxy is unreachable, it falls back to direct connection ([])
            self.assertEqual(get_kiwi_proxy_args(), [])
        finally:
            config.PACKAGE_PROXY_ENABLED = orig

    def test_prepare_description_for_build_injects_busybox_grep_ignore(self):
        from mama.builder import prepare_description_for_build
        import shutil
        import xml.etree.ElementTree as ET

        desc_dir = self.root_dir / "test-desc"
        desc_dir.mkdir()
        kiwi_file = desc_dir / "appliance.kiwi"
        kiwi_file.write_text("""<?xml version="1.0" encoding="utf-8"?>
<image name="test">
    <preferences>
        <packagemanager>zypper</packagemanager>
    </preferences>
    <packages type="image">
        <package name="openssh"/>
    </packages>
</image>""")

        staged_dir, is_staged = prepare_description_for_build(desc_dir, "x86_64", "test-os")
        try:
            self.assertTrue(is_staged)
            staged_kiwi = staged_dir / "appliance.kiwi"
            self.assertTrue(staged_kiwi.exists())
            tree = ET.parse(staged_kiwi)
            ignores = [ig.get("name") for ig in tree.getroot().findall(".//ignore")]
            self.assertIn("busybox-grep", ignores)
        finally:
            shutil.rmtree(staged_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
