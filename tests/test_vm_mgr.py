import pathlib
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from mama import config
from mama.machine import Machine
from mama.vm_mgr import build_qemu_cmd


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

    def test_build_qemu_cmd_defaults(self):
        cmd = build_qemu_cmd(self.mach, self.pid_path, self.vmlog_path)

        # Check binary name
        self.assertIn("qemu-system-x86_64", cmd[0] if "sudo" not in cmd[0] else cmd[1])

        # Check default memory is 4096 MB (not legacy 44 GB)
        m_idx = cmd.index("-m")
        self.assertEqual(cmd[m_idx + 1], "4096")

        # Check modern networking syntax
        netdev_idx = cmd.index("-netdev")
        self.assertEqual(cmd[netdev_idx + 1], "bridge,id=net0,br=br0")

        dev_idx = cmd.index("-device")
        self.assertEqual(cmd[dev_idx + 1], f"virtio-net-pci,netdev=net0,mac={self.mach.mac}")

        # Ensure legacy -net nic and -net bridge are NOT present
        self.assertNotIn("-net", cmd)

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
