import pathlib
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from mama.machine import Machine


class TestMachineStates(unittest.TestCase):
    def test_boot_states_include_new_names(self):
        new_states = ["power-on", "vm-start", "cycling", "waiting", "dhcp", "ipxe", "kernel", "initrd", "booting", "nfs"]
        for st in new_states:
            self.assertIn(st, Machine.BOOT_STATES)

    def test_is_booting(self):
        mach = Machine(name="m1", is_started=True)
        for st in ["power-on", "vm-start", "cycling", "waiting", "dhcp", "ipxe", "kernel", "initrd", "booting", "nfs"]:
            mach.state = st
            self.assertTrue(mach.is_booting(), f"Expected is_booting to be True for {st}")

        for st in ["online", "offline", "unresp", "failed", "stopping"]:
            mach.state = st
            self.assertFalse(mach.is_booting(), f"Expected is_booting to be False for {st}")

    def test_format_state(self):
        self.assertIn("online", Machine.format_state("online"))
        self.assertIn("offline", Machine.format_state("offline"))
        self.assertIn("waiting", Machine.format_state("waiting"))
        self.assertIn("power-on", Machine.format_state("power-on"))
        self.assertIn("vm-start", Machine.format_state("vm-start"))
        self.assertIn("cycling", Machine.format_state("cycling"))
        self.assertIn("dhcp", Machine.format_state("dhcp"))
        self.assertIn("ipxe", Machine.format_state("ipxe"))
        self.assertIn("kernel", Machine.format_state("kernel"))
        self.assertIn("initrd", Machine.format_state("initrd"))
        self.assertIn("nfs", Machine.format_state("nfs"))
        self.assertIn("failed", Machine.format_state("failed"))
        self.assertIn("unresp", Machine.format_state("unresp"))
        self.assertIn("unreach", Machine.format_state("unreach"))

        # Legacy backward compatibility checks
        self.assertIn("waiting-for-resources", Machine.format_state("waiting-for-resources"))
        self.assertIn("nfs", Machine.format_state("nfs-mounted"))
        self.assertIn("boot-failed", Machine.format_state("boot-failed"))

    def test_get_state_syncs_with_db_during_boot(self):
        from unittest.mock import MagicMock, patch

        mach = Machine(name="m1", is_started=True, state="dhcp", ip="192.168.1.50")
        mach.get_ip = MagicMock(return_value="192.168.1.50")
        mach.is_pingable = MagicMock(return_value=False)
        mach.get_ssh_status = MagicMock(return_value="closed")
        mach.is_nfs_mounted = MagicMock(return_value=False)

        with patch("mama.machine.MachineRepository.get_by_name") as mock_get_by_name:
            mock_get_by_name.return_value = {"name": "m1", "state": "kernel", "is_started": 1}
            state = mach.get_state()
            self.assertEqual(state, "kernel")
            self.assertEqual(mach.state, "kernel")

            # Check transition to booting
            mock_get_by_name.return_value = {"name": "m1", "state": "booting", "is_started": 1}
            state = mach.get_state()
            self.assertEqual(state, "booting")
            self.assertEqual(mach.state, "booting")

    def test_get_state_detects_external_stop(self):
        from unittest.mock import MagicMock, patch

        mach = Machine(name="m1", is_started=True, state="booting", ip="192.168.1.50")
        mach.get_ip = MagicMock(return_value="192.168.1.50")

        with patch("mama.machine.MachineRepository.get_by_name") as mock_get_by_name:
            mock_get_by_name.return_value = {"name": "m1", "state": "offline", "is_started": 0}
            state = mach.get_state()
            self.assertEqual(state, "offline")
            self.assertFalse(mach.is_started)

    @patch("mama.machine.MachineRepository.update_fields")
    @patch("mama.machine.subprocess.run")
    @patch("mama.machine.Util.is_root", return_value=False)
    def test_set_kernel_skips_sudo_when_initrd_already_readable(self, mock_is_root, mock_run, mock_update):
        from unittest.mock import MagicMock
        mach = Machine(name="m1", os="x86_64/test-os")
        mach.get_kernels = MagicMock(return_value={"6.4.0": "2026-11-01"})

        # Mock stat().st_mode on initrd to be 0o644 (already readable)
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.stat") as mock_stat:
                mock_stat.return_value = MagicMock(st_mode=0o100644)
                mach.set("kernel", "6.4.0")

        # sudo chmod should NOT be called
        mock_run.assert_not_called()
        self.assertEqual(mach.kernel, "6.4.0")
        mock_update.assert_called_with("m1", {"kernel": "6.4.0"})

    @patch("mama.machine.MachineRepository.update_fields")
    @patch("mama.machine.subprocess.run")
    @patch("mama.machine.Util.is_root", return_value=False)
    @patch("mama.machine.Os.is_runnable", return_value=True)
    def test_set_os_skips_sudo_when_initrd_already_readable(self, mock_runnable, mock_is_root, mock_run, mock_update):
        from unittest.mock import MagicMock
        mach = Machine(name="m1")
        mach.get_kernels = MagicMock(return_value={"6.4.0": "2026-11-01"})

        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.stat") as mock_stat:
                mock_stat.return_value = MagicMock(st_mode=0o100644)
                mach.set("os", "x86_64/test-os")

        # sudo chmod should NOT be called
        mock_run.assert_not_called()
        self.assertEqual(mach.os, "x86_64/test-os")
        self.assertEqual(mach.kernel, "6.4.0")


if __name__ == "__main__":
    unittest.main()
