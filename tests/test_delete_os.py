import pathlib
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from mama import config
from mama.cli_commands import cmd_delete_os
from mama.machine import Machine
from mama.repository import MachineRepository


class TestDeleteOs(unittest.TestCase):
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

    @patch("mama.cli_commands.subprocess.run")
    @patch("mama.cli_commands.Os.is_runnable", return_value=True)
    def test_delete_active_os_unsets_machine_os_and_kernel(self, mock_runnable, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        MachineRepository.upsert_machine({
            "name": "test-box",
            "mac": "00:11:22:33:44:55",
            "os": "x86_64/test-os",
            "kernel": "6.4.0-default",
            "_is_started": 0,
        })

        cmd_delete_os("test-box", "x86_64", "test-os")

        m = MachineRepository.get_by_name("test-box")
        self.assertEqual(m["os"], "")
        self.assertEqual(m["kernel"], "")

    @patch("mama.cli_commands.subprocess.run")
    @patch("mama.cli_commands.Os.is_runnable", return_value=True)
    def test_delete_inactive_os_preserves_machine_os_and_kernel(self, mock_runnable, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        MachineRepository.upsert_machine({
            "name": "test-box",
            "mac": "00:11:22:33:44:55",
            "os": "x86_64/other-os",
            "kernel": "6.4.0-default",
            "_is_started": 0,
        })

        # Deleting 'test-os', while machine is configured with 'other-os'
        cmd_delete_os("test-box", "x86_64", "test-os")

        m = MachineRepository.get_by_name("test-box")
        self.assertEqual(m["os"], "x86_64/other-os")
        self.assertEqual(m["kernel"], "6.4.0-default")

    @patch("mama.cli_commands.subprocess.run")
    @patch("mama.cli_commands.Os.is_runnable", return_value=True)
    def test_delete_running_active_os_stops_machine_first(self, mock_runnable, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        MachineRepository.upsert_machine({
            "name": "test-box",
            "mac": "00:11:22:33:44:55",
            "os": "x86_64/test-os",
            "kernel": "6.4.0-default",
            "_is_started": 1,
        })

        with patch("mama.machine.Machine.stop") as mock_stop:
            cmd_delete_os("test-box", "x86_64", "test-os")
            mock_stop.assert_called_once()

        m = MachineRepository.get_by_name("test-box")
        self.assertEqual(m["os"], "")
        self.assertEqual(m["kernel"], "")


if __name__ == "__main__":
    unittest.main()
