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

    def test_parse_sections(self):
        script = """
# Top-level comment
[MAMA]
echo "setup 1"
echo "setup 2"

[DEVICE]
cd /root/test
export FOO="bar"
./run.py

[MAMA]
echo "cleanup"
"""
        sections = Job.parse_sections(script)
        self.assertEqual(len(sections), 3)

        self.assertEqual(sections[0][0], "MAMA")
        self.assertEqual(sections[0][1], ['echo "setup 1"', 'echo "setup 2"'])

        self.assertEqual(sections[1][0], "DEVICE")
        self.assertEqual(sections[1][1], ['cd /root/test', 'export FOO="bar"', './run.py'])

        self.assertEqual(sections[2][0], "MAMA")
        self.assertEqual(sections[2][1], ['echo "cleanup"'])

    def test_execute_streams_device_section_in_single_ssh_call(self):
        script = """
[DEVICE]
cd /root/igt
export RUN_ID="123"
./test.sh
"""
        mach = MagicMock(spec=Machine)
        mach.name = "box1"
        mach.is_started = True
        mach.ssh_script = MagicMock(return_value=0)
        mach.stop = MagicMock(return_value=True)
        mach.load = MagicMock()

        job = Job("test-job")
        success = job.execute(script, mach=mach, worker=False)

        self.assertTrue(success)
        # Verify ssh_script was called ONCE for the whole block
        mach.ssh_script.assert_called_once()
        called_script = mach.ssh_script.call_args[1]["script"]
        self.assertIn("cd /root/igt", called_script)
        self.assertIn("export RUN_ID=\"123\"", called_script)
        self.assertIn("./test.sh", called_script)
        # Verify native exports are prepended
        self.assertIn("export JOB=test-job", called_script)
        self.assertIn("export MACH=box1", called_script)

    def test_job_get_env(self):
        mach = MagicMock(spec=Machine)
        mach.name = "dut1"
        worker = MagicMock(spec=Machine)
        worker.name = "builder1"

        job = Job("igt", args="--fast test1 test2")
        env = job.get_env(arch="x86_64", os_name="leap-16.0", worker=worker, mach=mach)

        self.assertEqual(env["ARCH"], "x86_64")
        self.assertEqual(env["OS"], "leap-16.0")
        self.assertEqual(env["JOB"], "igt")
        self.assertEqual(env["MACH"], "dut1")
        self.assertEqual(env["WORKER"], "builder1")
        self.assertEqual(env["ARGS"], "--fast test1 test2")
        self.assertEqual(env["ARG1"], "--fast")
        self.assertEqual(env["ARG2"], "test1")
        self.assertEqual(env["ARG3"], "test2")

    @patch("mama.job.subprocess.run")
    def test_execute_passes_native_env_to_mama_commands(self, mock_subproc):
        mock_subproc.return_value = MagicMock(returncode=0)
        mach = MagicMock(spec=Machine)
        mach.name = "dut1"

        script = """
[MAMA]
echo "Running locally"
"""
        job = Job("my-job", args="foo bar")
        job.execute(script, mach=mach, worker=False)

        mock_subproc.assert_called_once()
        env_passed = mock_subproc.call_args[1].get("env", {})
        self.assertEqual(env_passed.get("JOB"), "my-job")
        self.assertEqual(env_passed.get("MACH"), "dut1")
        self.assertEqual(env_passed.get("ARG1"), "foo")
        self.assertEqual(env_passed.get("ARG2"), "bar")

    def test_machine_ssh_script_invokes_bash_es(self):
        mach = Machine(name="box1", ip="192.168.2.55", is_started=True, state="online")
        mach.get_ssh_status = MagicMock(return_value="open")
        mach.get_ip = MagicMock(return_value="192.168.2.55")

        with patch("mama.net_mgr.run_ssh_cmd") as mock_run_ssh:
            mock_run_ssh.return_value = 0
            ret = mach.ssh_script("echo line1\necho line2\n", timeout_mins=20)
            self.assertEqual(ret, 0)

            mock_run_ssh.assert_called_once_with(
                ip="192.168.2.55",
                cmd="bash -es",
                timeout_mins=20,
                no_timeout=False,
                logfile=False,
                stdin_data="echo line1\necho line2\n",
            )


if __name__ == "__main__":
    unittest.main()
