import pathlib
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from mama.machine import Machine


class TestMachineStopCommand(unittest.TestCase):
    @patch("mama.machine.MachineRepository")
    @patch("mama.machine.subprocess.run")
    def test_stop_with_stopcmd_uses_halt(self, mock_subprocess, mock_repo):
        mach = Machine(name="m1", is_started=True, stopcmd="pwrctl off 1")
        mach.get_ssh_status = MagicMock(return_value="open")
        mach.ssh_cmd = MagicMock()
        mach.wait_for_state = MagicMock(return_value=True)
        mach.clear = MagicMock()
        mach.set = MagicMock()

        mach.stop()

        mach.ssh_cmd.assert_called_once_with("halt")
        mock_subprocess.assert_called_once_with("pwrctl off 1", shell=True)

    @patch("mama.machine.MachineRepository")
    @patch("mama.machine.subprocess.run")
    def test_stop_without_stopcmd_uses_poweroff(self, mock_subprocess, mock_repo):
        mach = Machine(name="m1", is_started=True, stopcmd="")
        mach.get_ssh_status = MagicMock(return_value="open")
        mach.ssh_cmd = MagicMock()
        mach.wait_for_state = MagicMock(return_value=True)
        mach.clear = MagicMock()
        mach.set = MagicMock()

        mach.stop()

        mach.ssh_cmd.assert_called_once_with("poweroff")
        mock_subprocess.assert_not_called()

    @patch("mama.machine.MachineRepository")
    @patch("mama.machine.subprocess.run")
    def test_stop_with_startcmd_only_uses_poweroff(self, mock_subprocess, mock_repo):
        # A machine that has a startcmd but NO stopcmd must use poweroff
        mach = Machine(name="m1", is_started=True, startcmd="pwrctl on 1", stopcmd="")
        mach.get_ssh_status = MagicMock(return_value="open")
        mach.ssh_cmd = MagicMock()
        mach.wait_for_state = MagicMock(return_value=True)
        mach.clear = MagicMock()
        mach.set = MagicMock()

        mach.stop()

        mach.ssh_cmd.assert_called_once_with("poweroff")
        mock_subprocess.assert_not_called()

    @patch("mama.machine.MachineRepository")
    @patch("mama.machine.subprocess.run")
    def test_stop_with_both_startcmd_and_stopcmd_uses_halt(self, mock_subprocess, mock_repo):
        mach = Machine(name="m1", is_started=True, startcmd="pwrctl on 1", stopcmd="pwrctl off 1")
        mach.get_ssh_status = MagicMock(return_value="open")
        mach.ssh_cmd = MagicMock()
        mach.wait_for_state = MagicMock(return_value=True)
        mach.clear = MagicMock()
        mach.set = MagicMock()

        mach.stop()

        mach.ssh_cmd.assert_called_once_with("halt")
        mock_subprocess.assert_called_once_with("pwrctl off 1", shell=True)


if __name__ == "__main__":
    unittest.main()
