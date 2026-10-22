import os
import pathlib
import sys
import tempfile
import time
import unittest

# Ensure src/ is on sys.path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from mama.cli_parser import format_duration
from mama.machine import Machine
from mama.repository import MachineRepository
from mama import config


class TestFormatDuration(unittest.TestCase):
    def test_format_duration_basic(self):
        self.assertEqual(format_duration(-5), "0s")
        self.assertEqual(format_duration(0), "0s")
        self.assertEqual(format_duration(45), "45s")
        self.assertEqual(format_duration(60), "1m")
        self.assertEqual(format_duration(75), "1m 15s")
        self.assertEqual(format_duration(3600), "1h")
        self.assertEqual(format_duration(3660), "1h 1m")
        self.assertEqual(format_duration(86400), "1d")

    def test_format_duration_max_units(self):
        # 2 hours, 15 minutes, 30 seconds
        dur = 2 * 3600 + 15 * 60 + 30
        self.assertEqual(format_duration(dur, max_units=2), "2h 15m")
        self.assertEqual(format_duration(dur, max_units=1), "2h")
        self.assertEqual(format_duration(dur), "2h 15m 30s")

        # 3 days, 4 hours, 5 minutes, 6 seconds
        dur_days = 3 * 86400 + 4 * 3600 + 5 * 60 + 6
        self.assertEqual(format_duration(dur_days, max_units=2), "3d 4h")


class TestMachineUptime(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.orig_mama_path = getattr(config, "MAMA_PATH", "/usr/share/mama")
        self.orig_db_file = getattr(config, "DB_FILE", f"{self.orig_mama_path}/mama.db")
        config.MAMA_PATH = self.tmp_dir.name
        config.DB_FILE = os.path.join(self.tmp_dir.name, "mama.db")
        MachineRepository.init_db()

    def tearDown(self):
        config.MAMA_PATH = self.orig_mama_path
        config.DB_FILE = self.orig_db_file
        self.tmp_dir.cleanup()

    def test_uptime_when_not_started(self):
        mach = Machine(name="test1", mac="00:11:22:33:44:55", is_started=False)
        MachineRepository.add_machine(mach)
        self.assertIsNone(mach.get_uptime())

    def test_uptime_with_started_at(self):
        now = int(time.time())
        mach = Machine(name="test2", mac="00:11:22:33:44:56", is_started=True, started_at=now - 120)
        MachineRepository.add_machine(mach)

        uptime = mach.get_uptime()
        self.assertIsNotNone(uptime)
        self.assertGreaterEqual(uptime, 120)
        self.assertLess(uptime, 130)

    def test_uptime_fallback_to_boot_history(self):
        now = int(time.time())
        mach = Machine(name="test3", mac="00:11:22:33:44:57", is_started=True, started_at=None)
        MachineRepository.add_machine(mach)
        # Clear started_at explicitly in DB to test fallback
        MachineRepository.update_fields("test3", {"started_at": None})

        # Add open boot_history record
        MachineRepository.record_start_time("test3", "default", "bm", 45, start_time=now - 300)
        # Clear started_at synced by record_start_time to specifically test fallback
        MachineRepository.update_fields("test3", {"started_at": None})

        reloaded = Machine.get_by_name("test3")
        self.assertIsNotNone(reloaded)
        self.assertTrue(reloaded.is_started)
        uptime = reloaded.get_uptime()
        self.assertIsNotNone(uptime)
        self.assertGreaterEqual(uptime, 300)
        self.assertLess(uptime, 310)


if __name__ == "__main__":
    unittest.main()
