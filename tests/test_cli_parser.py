import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from mama.cli_parser import format_duration, setup_cli_parser


class TestCliParser(unittest.TestCase):
    def setUp(self):
        self.parser = setup_cli_parser()

    def test_format_duration(self):
        self.assertEqual(format_duration(0), "0s")
        self.assertEqual(format_duration(-5), "0s")
        self.assertEqual(format_duration(45), "45s")
        self.assertEqual(format_duration(125), "2m 5s")
        self.assertEqual(format_duration(3665), "1h 1m 5s")
        self.assertEqual(format_duration(90065), "1d 1h 1m 5s")

        # Test max_units limit
        self.assertEqual(format_duration(90065, max_units=1), "1d")
        self.assertEqual(format_duration(90065, max_units=2), "1d 1h")

    def test_parse_list_defaults(self):
        args = self.parser.parse_args(["list"])
        self.assertEqual(args.command, "list")
        self.assertEqual(args.item, "machines")
        self.assertFalse(args.verbose)
        self.assertFalse(args.fast)

    def test_parse_list_flags(self):
        args = self.parser.parse_args(["list", "-v", "--fast"])
        self.assertEqual(args.command, "list")
        self.assertTrue(args.verbose)
        self.assertTrue(args.fast)

    def test_parse_start_and_stop(self):
        args_start = self.parser.parse_args(["start", "box1"])
        self.assertEqual(args_start.command, "start")
        self.assertEqual(args_start.machine, "box1")

        args_stop = self.parser.parse_args(["stop", "box1"])
        self.assertEqual(args_stop.command, "stop")
        self.assertEqual(args_stop.machine, "box1")

    def test_parse_reserve_and_release(self):
        args_res = self.parser.parse_args(["reserve", "box1"])
        self.assertEqual(args_res.command, "reserve")
        self.assertEqual(args_res.machine, "box1")

        args_rel = self.parser.parse_args(["release", "box1", "--force"])
        self.assertEqual(args_rel.command, "release")
        self.assertEqual(args_rel.machine, "box1")
        self.assertTrue(args_rel.force)

    def test_parse_job_run(self):
        args = self.parser.parse_args([
            "job", "igt", "run", "box1", "x86_64", "leap-16.0",
            "--timeout", "45",
            "--args", "test_param",
        ])
        self.assertEqual(args.command, "job")
        self.assertEqual(args.job_name, "igt")
        self.assertEqual(args.job_action, "run")
        self.assertEqual(args.machine, "box1")
        self.assertEqual(args.arch, "x86_64")
        self.assertEqual(args.os, "leap-16.0")
        self.assertEqual(args.timeout, 45)
        self.assertEqual(args.args, "test_param")

    def test_parse_dhcp_sync(self):
        args = self.parser.parse_args(["dhcp", "sync"])
        self.assertEqual(args.command, "dhcp")
        self.assertEqual(args.dhcp_action, "sync")


if __name__ == "__main__":
    unittest.main()
