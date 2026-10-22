import pathlib
import sys
import unittest

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


if __name__ == "__main__":
    unittest.main()
