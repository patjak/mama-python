import pathlib
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from mama import config
from mama.machine import Machine, _HELD_MACHINE_LOCKS


class TestMachineLock(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        config.MAMA_PATH = self.temp_dir.name
        _HELD_MACHINE_LOCKS.clear()

    def tearDown(self):
        self.temp_dir.cleanup()
        _HELD_MACHINE_LOCKS.clear()

    def test_lock_acquires_and_releases(self):
        mach = Machine(name="box1")
        self.assertEqual(_HELD_MACHINE_LOCKS.get("box1", 0), 0)

        with mach.lock():
            self.assertEqual(_HELD_MACHINE_LOCKS.get("box1", 0), 1)
            lock_file = pathlib.Path(self.temp_dir.name) / "locks" / "box1.lock"
            self.assertTrue(lock_file.exists())

        self.assertEqual(_HELD_MACHINE_LOCKS.get("box1", 0), 0)

    def test_lock_reentrancy_in_same_process(self):
        mach = Machine(name="box1")

        with mach.lock():
            self.assertEqual(_HELD_MACHINE_LOCKS.get("box1", 0), 1)
            with mach.lock():
                self.assertEqual(_HELD_MACHINE_LOCKS.get("box1", 0), 2)
            self.assertEqual(_HELD_MACHINE_LOCKS.get("box1", 0), 1)

        self.assertEqual(_HELD_MACHINE_LOCKS.get("box1", 0), 0)

    @patch("mama.machine.MachineRepository")
    def test_stop_acquires_lock(self, mock_repo):
        mach = Machine(name="box1", is_started=False)
        mach.clear = MagicMock()

        with patch.object(mach, "lock", wraps=mach.lock) as mock_lock:
            mach.stop()
            mock_lock.assert_called_once()

    @patch("mama.machine.MachineRepository")
    def test_start_calls_stop_reentrant_without_deadlock(self, mock_repo):
        # When a machine fails to boot or is in unreach state, start calls stop.
        # This must succeed without deadlocking on the machine lock.
        mach = Machine(name="box1", is_started=False, os="x86_64/test-os", kernel="6.1.0")
        mach.get_state = MagicMock(return_value="offline")
        mach.wait_for_resources = MagicMock(return_value=True)
        mach.wait_for_state = MagicMock(return_value=False)  # triggers boot failure
        mach.clear = MagicMock()

        # Should execute cleanly without deadlocking
        res = mach.start()
        self.assertFalse(res)
        self.assertEqual(_HELD_MACHINE_LOCKS.get("box1", 0), 0)

    def test_os_lock_acquires_and_releases(self):
        from mama.os_mgr import Os
        lock_file = pathlib.Path(self.temp_dir.name) / "locks" / "os-x86_64-tumbleweed.lock"
        self.assertFalse(lock_file.exists())

        with Os.lock("x86_64", "tumbleweed", shared=False):
            self.assertTrue(lock_file.exists())

        with Os.lock("x86_64", "tumbleweed", shared=True):
            self.assertTrue(lock_file.exists())


if __name__ == "__main__":
    unittest.main()
