import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from mama import config
from mama.machine import Machine
from mama.repository import MachineRepository


class TestAtomicReservation(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "test_mama.db"
        config.DB_FILE = str(self.db_path)
        MachineRepository.init_db()

        # Insert a sample machine for reservation tests
        MachineRepository.upsert_machine({
            "name": "test-box",
            "mac": "00:11:22:33:44:55",
            "reservation": "",
        })

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_try_reserve_success(self):
        success, holder = MachineRepository.try_reserve("test-box", "alice")
        self.assertTrue(success)
        self.assertEqual(holder, "alice")

        m = MachineRepository.get_by_name("test-box")
        self.assertEqual(m["reservation"], "alice")

    def test_try_reserve_conflict(self):
        # Alice reserves first
        success, holder = MachineRepository.try_reserve("test-box", "alice")
        self.assertTrue(success)

        # Bob tries to reserve the same machine
        success2, holder2 = MachineRepository.try_reserve("test-box", "bob")
        self.assertFalse(success2)
        self.assertEqual(holder2, "alice")

        # Machine remains reserved by Alice
        m = MachineRepository.get_by_name("test-box")
        self.assertEqual(m["reservation"], "alice")

    def test_release_reservation_owner(self):
        MachineRepository.try_reserve("test-box", "alice")

        success, err = MachineRepository.release_reservation("test-box", user="alice")
        self.assertTrue(success)
        self.assertEqual(err, "")

        m = MachineRepository.get_by_name("test-box")
        self.assertEqual(m["reservation"], "")

    def test_release_reservation_non_owner_without_force(self):
        MachineRepository.try_reserve("test-box", "alice")

        success, err = MachineRepository.release_reservation("test-box", user="bob", force=False)
        self.assertFalse(success)
        self.assertIn("Held by 'alice'", err)

        # Still reserved by Alice
        m = MachineRepository.get_by_name("test-box")
        self.assertEqual(m["reservation"], "alice")

    def test_release_reservation_force(self):
        MachineRepository.try_reserve("test-box", "alice")

        success, err = MachineRepository.release_reservation("test-box", user="bob", force=True)
        self.assertTrue(success)

        m = MachineRepository.get_by_name("test-box")
        self.assertEqual(m["reservation"], "")

    def test_machine_reserve_instance_method(self):
        mach = Machine(name="test-box")
        success, holder = mach.reserve("alice")
        self.assertTrue(success)
        self.assertEqual(mach.reservation, "alice")

        # Another machine object trying to reserve
        mach2 = Machine(name="test-box")
        success2, holder2 = mach2.reserve("bob")
        self.assertFalse(success2)
        self.assertEqual(holder2, "alice")


if __name__ == "__main__":
    unittest.main()
