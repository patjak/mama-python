import pathlib
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from mama.cli_parser import setup_cli_parser
from mama.job import Job
from mama.machine import Machine


class TestJobTimeoutCli(unittest.TestCase):
    def setUp(self):
        self.parser = setup_cli_parser()

    def test_job_run_timeout_flag(self):
        args = self.parser.parse_args(["job", "igt", "run", "m1", "x86_64", "leap-16.0", "--timeout=15"])
        self.assertEqual(args.command, "job")
        self.assertEqual(args.job_name, "igt")
        self.assertEqual(args.job_action, "run")
        self.assertEqual(args.machine, "m1")
        self.assertEqual(args.timeout, 15)

    def test_job_prepare_timeout_flag(self):
        args = self.parser.parse_args(["job", "igt", "prepare", "x86_64", "leap-16.0", "--timeout=25"])
        self.assertEqual(args.command, "job")
        self.assertEqual(args.job_name, "igt")
        self.assertEqual(args.job_action, "prepare")
        self.assertEqual(args.timeout, 25)

    def test_job_timeout_default_is_none(self):
        args = self.parser.parse_args(["job", "igt", "run", "m1", "x86_64", "leap-16.0"])
        self.assertIsNone(getattr(args, "timeout", None))


class TestJobTimeoutExecution(unittest.TestCase):
    def test_job_init_with_timeout(self):
        job = Job("igt", timeout=15)
        self.assertEqual(job.timeout, 15)

    def test_machine_ssh_cmd_custom_timeout(self):
        mach = Machine(name="m1", ip="192.168.2.10", is_started=True, state="online")
        mach.get_ssh_status = MagicMock(return_value="open")
        mach.get_ip = MagicMock(return_value="192.168.2.10")

        with patch("mama.net_mgr.run_ssh_cmd") as mock_run_ssh:
            mock_run_ssh.return_value = 0
            # Test default timeout (60 mins)
            mach.ssh_cmd("uptime")
            mock_run_ssh.assert_called_once_with(
                ip="192.168.2.10",
                cmd="uptime",
                timeout_mins=60,
                no_timeout=False,
                logfile=False,
            )

        with patch("mama.net_mgr.run_ssh_cmd") as mock_run_ssh:
            mock_run_ssh.return_value = 0
            # Test custom timeout (15 mins)
            mach.ssh_cmd("uptime", timeout_mins=15)
            mock_run_ssh.assert_called_once_with(
                ip="192.168.2.10",
                cmd="uptime",
                timeout_mins=15,
                no_timeout=False,
                logfile=False,
            )


if __name__ == "__main__":
    unittest.main()
