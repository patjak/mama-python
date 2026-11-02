import pathlib
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from mama import config
from mama.dhcp import DnsmasqManager
from mama.repository import MachineRepository


class TestDhcp(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.leases_file = pathlib.Path(self.temp_dir.name) / "dnsmasq.leases"
        self.db_file = pathlib.Path(self.temp_dir.name) / "test.db"

        self.orig_leases = config.DNSMASQ_LEASES_FILE
        self.orig_db = config.DB_FILE
        config.DNSMASQ_LEASES_FILE = str(self.leases_file)
        config.DB_FILE = str(self.db_file)
        MachineRepository.init_db()

    def tearDown(self):
        config.DNSMASQ_LEASES_FILE = self.orig_leases
        config.DB_FILE = self.orig_db
        self.temp_dir.cleanup()

    def test_generate_config_contains_core_directives(self):
        conf_text = DnsmasqManager.generate_config()

        self.assertIn(f"interface={config.DHCP_INTERFACE}", conf_text)
        self.assertIn(f"dhcp-range={config.DHCP_RANGE_START},{config.DHCP_RANGE_END}", conf_text)
        self.assertIn(f"tftp-root={config.TFTP_DIR}", conf_text)
        self.assertIn(f"dhcp-option=option:router,{config.MAMA_HOST}", conf_text)

    def test_generate_config_with_mtu(self):
        orig_mtu = getattr(config, "DHCP_MTU", None)
        try:
            config.DHCP_MTU = 9000
            conf_text = DnsmasqManager.generate_config()
            self.assertIn("dhcp-option=option:mtu,9000", conf_text)

            config.DHCP_MTU = None
            conf_text_no_mtu = DnsmasqManager.generate_config()
            self.assertNotIn("option:mtu", conf_text_no_mtu)
        finally:
            config.DHCP_MTU = orig_mtu

    def test_generate_autoexec_ipxe(self):
        success = DnsmasqManager.generate_autoexec_ipxe()
        self.assertTrue(success)

        autoexec_file = pathlib.Path(config.TFTP_DIR) / "autoexec.ipxe"
        self.assertTrue(autoexec_file.exists())
        ipxe_script = autoexec_file.read_text(encoding="utf-8")
        self.assertTrue(ipxe_script.startswith("#!ipxe"))
        self.assertIn(f"http://{config.MAMA_HOST}:8080/boot.py?mac=${{mac}}", ipxe_script)

    def test_get_active_leases(self):
        self.leases_file.write_text(
            "1700000000 00:11:22:33:44:55 192.168.2.105 client-1 *\n"
            "1700000000 66:77:88:99:aa:bb 192.168.2.106 client-2 *\n",
            encoding="utf-8",
        )

        leases = DnsmasqManager.get_active_leases()
        self.assertEqual(len(leases), 2)
        self.assertEqual(leases["00:11:22:33:44:55"], "192.168.2.105")
        self.assertEqual(leases["66:77:88:99:aa:bb"], "192.168.2.106")

    def test_sync_leases_to_db(self):
        # Insert a started machine with MAC 00:11:22:33:44:55 and empty IP
        MachineRepository.upsert_machine({
            "name": "sync-box",
            "mac": "00:11:22:33:44:55",
            "ip": "",
            "_is_started": 1,
            "state": "power-on",
        })

        self.leases_file.write_text(
            "1700000000 00:11:22:33:44:55 192.168.2.150 sync-box *\n",
            encoding="utf-8",
        )

        updated = DnsmasqManager.sync_leases_to_db()
        self.assertEqual(updated, 1)

        m = MachineRepository.get_by_name("sync-box")
        self.assertEqual(m["ip"], "192.168.2.150")
        self.assertEqual(m["state"], "dhcp")


if __name__ == "__main__":
    unittest.main()
