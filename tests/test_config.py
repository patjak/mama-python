import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from mama import config


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.conf_file = pathlib.Path(self.temp_dir.name) / "mama.conf"

        # Save original config values to restore in tearDown
        self.orig_host = config.MAMA_HOST
        self.orig_dhcp_start = config.DHCP_RANGE_START
        self.orig_vm_ram = config.VM_DEFAULT_RAM_MB
        self.orig_debug = config.DEBUG

    def tearDown(self):
        # Restore original config values
        config.MAMA_HOST = self.orig_host
        config.DHCP_RANGE_START = self.orig_dhcp_start
        config.VM_DEFAULT_RAM_MB = self.orig_vm_ram
        config.DEBUG = self.orig_debug

        for var in ["MAMA_HOST", "MAMA_CONF", "VM_DEFAULT_RAM_MB", "DEBUG"]:
            if var in os.environ:
                del os.environ[var]

        self.temp_dir.cleanup()

    def test_load_config_from_file_with_section(self):
        self.conf_file.write_text(
            """
[mama]
mama_host = 10.10.10.1
dhcp_range_start = 10.10.10.50
vm_default_ram_mb = 8192
debug = true
""",
            encoding="utf-8",
        )

        config.load_config(conf_path=str(self.conf_file))

        self.assertEqual(config.MAMA_HOST, "10.10.10.1")
        self.assertEqual(config.DHCP_RANGE_START, "10.10.10.50")
        self.assertEqual(config.VM_DEFAULT_RAM_MB, 8192)
        self.assertTrue(config.DEBUG)

    def test_load_config_from_file_flat_syntax(self):
        self.conf_file.write_text(
            """
mama_host = 172.16.0.1
vm_default_ram_mb = 2048
debug = false
""",
            encoding="utf-8",
        )

        config.load_config(conf_path=str(self.conf_file))

        self.assertEqual(config.MAMA_HOST, "172.16.0.1")
        self.assertEqual(config.VM_DEFAULT_RAM_MB, 2048)
        self.assertFalse(config.DEBUG)

    def test_env_var_override_takes_precedence(self):
        self.conf_file.write_text(
            """
[mama]
mama_host = 10.10.10.1
vm_default_ram_mb = 4096
""",
            encoding="utf-8",
        )

        os.environ["MAMA_HOST"] = "10.99.99.1"
        os.environ["VM_DEFAULT_RAM_MB"] = "16384"

        config.load_config(conf_path=str(self.conf_file))

        self.assertEqual(config.MAMA_HOST, "10.99.99.1")
        self.assertEqual(config.VM_DEFAULT_RAM_MB, 16384)

    def test_mama_conf_env_var(self):
        self.conf_file.write_text(
            """
[mama]
mama_host = 192.168.100.1
""",
            encoding="utf-8",
        )

        os.environ["MAMA_CONF"] = str(self.conf_file)
        config.load_config()

        self.assertEqual(config.MAMA_HOST, "192.168.100.1")


if __name__ == "__main__":
    unittest.main()
