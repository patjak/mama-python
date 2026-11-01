import pathlib
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from mama import config
from mama.machine import Machine
from mama.vm_mgr import build_qemu_cmd, get_default_vm_ram_mb


class TestVmMgr(unittest.TestCase):
    def setUp(self):
        self.mach = Machine(
            name="vm-test",
            mac="52:54:00:12:34:56",
            os="x86_64/test-os",
            kernel="6.4.0",
        )
        self.mach.get_kernel_path = MagicMock(return_value="/tmp/test_kernels")
        self.pid_path = pathlib.Path("/tmp/test.pid")
        self.vmlog_path = pathlib.Path("/tmp/test.vmlog")

        self.orig_ram = config.VM_DEFAULT_RAM_MB
        self.orig_reserve = config.VM_HOST_RAM_RESERVE_MB

    def tearDown(self):
        config.VM_DEFAULT_RAM_MB = self.orig_ram
        config.VM_HOST_RAM_RESERVE_MB = self.orig_reserve

    def test_get_default_vm_ram_mb_auto(self):
        config.VM_DEFAULT_RAM_MB = "auto"
        config.VM_HOST_RAM_RESERVE_MB = 4096

        # Simulate a host with 64 GB (65536 MB) of RAM
        with patch("os.sysconf") as mock_sysconf:
            def fake_sysconf(name):
                if name == "SC_PAGE_SIZE":
                    return 4096
                if name == "SC_PHYS_PAGES":
                    return 65536 * 1024 * 1024 // 4096
                return 0
            mock_sysconf.side_effect = fake_sysconf

            ram = get_default_vm_ram_mb()
            # 65536 - 4096 = 61440 MB
            self.assertEqual(ram, 61440)

    def test_get_default_vm_ram_mb_floor(self):
        config.VM_DEFAULT_RAM_MB = "auto"
        config.VM_HOST_RAM_RESERVE_MB = 4096

        # Simulate a small host with only 4 GB (4096 MB) of RAM
        with patch("os.sysconf") as mock_sysconf:
            def fake_sysconf(name):
                if name == "SC_PAGE_SIZE":
                    return 4096
                if name == "SC_PHYS_PAGES":
                    return 4096 * 1024 * 1024 // 4096
                return 0
            mock_sysconf.side_effect = fake_sysconf

            ram = get_default_vm_ram_mb()
            # 4096 - 4096 = 0 -> clamped to minimum floor 2048 MB
            self.assertEqual(ram, 2048)

    def test_get_default_vm_ram_mb_explicit(self):
        config.VM_DEFAULT_RAM_MB = 8192
        self.assertEqual(get_default_vm_ram_mb(), 8192)

    def test_build_qemu_cmd_defaults(self):
        config.VM_DEFAULT_RAM_MB = 4096
        cmd = build_qemu_cmd(self.mach, self.pid_path, self.vmlog_path)

        # Check binary name without sudo
        self.assertEqual(cmd[0], "qemu-system-x86_64")
        self.assertNotIn("sudo", cmd)

        # Check configured memory
        m_idx = cmd.index("-m")
        self.assertEqual(cmd[m_idx + 1], "4096")

        # Check modern networking syntax
        netdev_idx = cmd.index("-netdev")
        self.assertEqual(cmd[netdev_idx + 1], "bridge,id=net0,br=br0")

        dev_idx = cmd.index("-device")
        self.assertEqual(cmd[dev_idx + 1], f"virtio-net-pci,netdev=net0,mac={self.mach.mac}")

        # Ensure legacy -net nic and -net bridge are NOT present
        self.assertNotIn("-net", cmd)

    @patch("mama.vm_mgr.is_in_kvm_group", return_value=False)
    def test_start_vm_warns_when_not_in_kvm_group(self, mock_kvm):
        from mama.vm_mgr import start_vm
        mach = Machine(name="vm-1", os="x86_64/test", kernel="6.4")
        mach.error = MagicMock()
        with patch("mama.vm_mgr.get_vm_pid", return_value=None):
            res = start_vm(mach)
            self.assertFalse(res)
            mach.error.assert_called_once()
            self.assertIn("kvm", mach.error.call_args[0][0])

    def test_build_qemu_cmd_custom_memory_override(self):
        self.mach.vm_params = "-m 8192"
        cmd = build_qemu_cmd(self.mach, self.pid_path, self.vmlog_path)

        # Ensure user's -m 8192 is used and not duplicated
        m_occurrences = [i for i, arg in enumerate(cmd) if arg == "-m"]
        self.assertEqual(len(m_occurrences), 1)
        self.assertEqual(cmd[m_occurrences[0] + 1], "8192")

    def test_build_qemu_cmd_custom_smp_override(self):
        self.mach.vm_params = "-smp 2"
        cmd = build_qemu_cmd(self.mach, self.pid_path, self.vmlog_path)

        smp_occurrences = [i for i, arg in enumerate(cmd) if arg == "-smp"]
        self.assertEqual(len(smp_occurrences), 1)
        self.assertEqual(cmd[smp_occurrences[0] + 1], "2")

    @patch("pathlib.Path.exists")
    @patch("platform.machine", return_value="x86_64")
    def test_build_qemu_cmd_kvm_flag(self, mock_plat, mock_exists):
        mock_exists.return_value = True
        cmd = build_qemu_cmd(self.mach, self.pid_path, self.vmlog_path)
        self.assertIn("-enable-kvm", cmd)


if __name__ == "__main__":
    unittest.main()
