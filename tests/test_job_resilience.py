import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from mama import config
from mama.job import Job
from mama.job_queue import JobQueue
from mama.machine import Machine
from mama.repository import MachineRepository


class TestJobResilience(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_file = pathlib.Path(self.temp_dir.name) / "test.db"
        self.orig_db = config.DB_FILE
        self.orig_mama_path = config.MAMA_PATH
        config.DB_FILE = str(self.db_file)
        config.MAMA_PATH = self.temp_dir.name
        MachineRepository.init_db()

        MachineRepository.upsert_machine({
            "name": "box1",
            "mac": "00:11:22:33:44:55",
            "job": "",
            "job_pid": "",
        })

    def tearDown(self):
        config.DB_FILE = self.orig_db
        config.MAMA_PATH = self.orig_mama_path
        self.temp_dir.cleanup()

    def test_strict_fifo_queue_claiming(self):
        pid1 = 10001
        pid2 = 10002

        with patch("mama.job_queue.JobQueue.is_pid_alive", return_value=True):
            # Job 1 arrives first
            JobQueue.enqueue("box1", "job-1", pid=pid1)
            # Job 2 arrives second
            JobQueue.enqueue("box1", "job-2", pid=pid2)

            # Job 2 tries to jump the queue -> MUST FAIL because Job 1 is at head
            claimed_by_2 = JobQueue.try_claim_machine("box1", "job-2", pid=pid2)
            self.assertFalse(claimed_by_2)

            # Machine is still idle and Job 1 remains head
            m = MachineRepository.get_by_name("box1")
            self.assertEqual(m["job"], "")

            # Job 1 claims machine -> MUST SUCCEED
            claimed_by_1 = JobQueue.try_claim_machine("box1", "job-1", pid=pid1)
            self.assertTrue(claimed_by_1)

            # Machine is now busy with Job 1
            m = MachineRepository.get_by_name("box1")
            self.assertEqual(m["job"], "job-1")
            self.assertEqual(m["job_pid"], str(pid1))

            # Job 2 tries to claim while Job 1 is running -> MUST FAIL
            self.assertFalse(JobQueue.try_claim_machine("box1", "job-2", pid=pid2))

            # Job 1 finishes and clears machine
            MachineRepository.update_fields("box1", {"job": "", "job_pid": ""})

            # Now Job 2 is head and machine is idle -> MUST SUCCEED
            self.assertTrue(JobQueue.try_claim_machine("box1", "job-2", pid=pid2))
            m = MachineRepository.get_by_name("box1")
            self.assertEqual(m["job"], "job-2")
            self.assertEqual(m["job_pid"], str(pid2))

    @patch("mama.job.select_machine")
    def test_job_keyboard_interrupt_cleans_up(self, mock_select):
        mach = MagicMock(spec=Machine)
        mach.name = "box1"
        mach.job = ""
        mach.job_pid = ""
        mach.stop = MagicMock()
        mach.save = MagicMock()
        mock_select.return_value = mach

        # Create dummy run.job
        jobs_dir = pathlib.Path(config.MAMA_PATH) / "jobs" / "test-job"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        (jobs_dir / "run.job").write_text("echo test\n", encoding="utf-8")

        job = Job("test-job")
        job.arch = "x86_64"
        job.os = "test-os"
        job.mach = "box1"

        # Simulate KeyboardInterrupt during execute()
        with patch.object(job, "execute", side_effect=KeyboardInterrupt):
            with patch("mama.job_queue.JobQueue.try_claim_machine", return_value=True):
                res = job.execute_run_job()
                self.assertFalse(res)

        # Verify mach.stop() was called in finally
        mach.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
