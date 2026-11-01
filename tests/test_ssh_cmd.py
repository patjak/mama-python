import pathlib
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from mama import net_mgr
from mama.machine import Machine


class TestSshCmd(unittest.TestCase):
    @patch("mama.net_mgr.subprocess.run")
    def test_run_ssh_cmd_uses_dash_T_by_default(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        ret = net_mgr.run_ssh_cmd("192.168.2.10", "uname -a")
        self.assertEqual(ret, 0)

        called_args = mock_run.call_args[0][0]
        self.assertIn("-T", called_args)
        self.assertNotIn("-t", called_args)

    @patch("mama.net_mgr.subprocess.run")
    def test_run_ssh_cmd_uses_dash_t_when_pty_requested(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        ret = net_mgr.run_ssh_cmd("192.168.2.10", "top", allocate_pty=True)
        self.assertEqual(ret, 0)

        called_args = mock_run.call_args[0][0]
        self.assertIn("-t", called_args)
        self.assertNotIn("-T", called_args)

    @patch("mama.machine.net_mgr.run_ssh_cmd")
    def test_machine_ssh_cmd_forwards_allocate_pty(self, mock_run_ssh):
        mock_run_ssh.return_value = 0
        mach = Machine(name="m1", is_started=True, ip="192.168.2.15")
        mach.get_ssh_status = MagicMock(return_value="open")
        mach.get_ip = MagicMock(return_value="192.168.2.15")

        mach.ssh_cmd("htop", allocate_pty=True)
        mock_run_ssh.assert_called_once_with(
            ip="192.168.2.15",
            cmd="htop",
            timeout_mins=60,
            no_timeout=False,
            logfile=False,
            allocate_pty=True,
        )


if __name__ == "__main__":
    unittest.main()
