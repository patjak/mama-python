import pathlib
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from mama import config
from mama.cli_commands import cmd_new, cmd_set
from mama.repository import MachineRepository


class TestInventoryCommands(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_file = pathlib.Path(self.temp_dir.name) / "test.db"
        self.orig_db = config.DB_FILE
        self.orig_mama_path = config.MAMA_PATH

        config.DB_FILE = str(self.db_file)
        config.MAMA_PATH = self.temp_dir.name
        MachineRepository.init_db()

    def tearDown(self):
        from mama.logger import Log
        Log.set_file(False)
        config.DB_FILE = self.orig_db
        config.MAMA_PATH = self.orig_mama_path
        self.temp_dir.cleanup()

    @patch("mama.cli_commands.DnsmasqManager.reload_or_restart")
    @patch("mama.cli_commands.subprocess.run")
    @patch("mama.cli_commands.Util.is_root", return_value=False)
    @patch("mama.cli_commands.Arch.get_buildable", return_value=["x86_64"])
    @patch("questionary.text")
    @patch("questionary.select")
    @patch("questionary.confirm")
    def test_cmd_new_unprivileged(
        self, mock_confirm, mock_select, mock_text, mock_archs, mock_is_root, mock_run, mock_dnsmasq
    ):
        mock_text.return_value.ask.side_effect = ["box-dev", "00:11:22:33:44:55"]
        mock_select.return_value.ask.return_value = "x86_64"
        mock_confirm.return_value.ask.return_value = True

        cmd_new()

        # Machine must be created in SQLite
        m = MachineRepository.get_by_name("box-dev")
        self.assertIsNotNone(m)
        self.assertEqual(m["mac"], "00:11:22:33:44:55")

        # No sudo command and no privileged dnsmasq reload should be invoked
        mock_run.assert_not_called()
        mock_dnsmasq.assert_not_called()

    @patch("mama.cli_commands.DnsmasqManager.reload_or_restart")
    @patch("mama.cli_commands.Util.is_root", return_value=False)
    def test_cmd_set_ip_unprivileged(self, mock_is_root, mock_dnsmasq):
        MachineRepository.upsert_machine({
            "name": "box-dev",
            "mac": "00:11:22:33:44:55",
            "ip": "",
        })

        cmd_set(machine="box-dev", attr="ip", val_list=["192.168.2.120"])

        # IP must be updated in SQLite
        m = MachineRepository.get_by_name("box-dev")
        self.assertEqual(m["ip"], "192.168.2.120")

        # Privileged dnsmasq reload should be skipped for non-root users
        mock_dnsmasq.assert_not_called()


if __name__ == "__main__":
    unittest.main()
