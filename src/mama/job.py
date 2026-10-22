from __future__ import annotations

import os
import pathlib
import subprocess
import time
from typing import Any, Union

from rich.console import Console

from . import config
from .job_queue import JobQueue
from .logger import Log, debug, error, fatal, out
from .os_mgr import Os
from .machine import Machine
from .repository import select_machine


class Job:
    def __init__(
        self,
        name: str,
        args: str = "",
        no_ssh_timeout: bool = False,
        no_retry: bool = False,
        timeout: Optional[int] = None,
    ) -> None:
        self.name: str = name
        self.type: str = ""
        self.arch: str = ""
        self.os: str = ""
        self.mach: Any = False
        self.args: str = args
        self.no_ssh_timeout: bool = no_ssh_timeout
        self.no_retry: bool = no_retry
        self.timeout: Optional[int] = timeout

    def _is_active_job(self, job_str: str, mach: Any = False) -> bool:
        machs = Machine.get_all()

        for m in machs:
            if not m.job:
                continue

            j1 = m.job.split()
            j2 = job_str.split()

            if len(j1) != 3 or len(j2) != 3:
                continue

            match = (j1[1] == j2[1]) and (j1[2] == j2[2])
            if not match:
                continue

            if mach is False or mach is None:
                return True
            elif hasattr(mach, "name") and mach.name == m.name:
                return True

        return False

    def _wait_for_active_job(self, job_str: str, mach: Any = False) -> None:
        if self._is_active_job(job_str, mach):
            self.out(f"Waiting for running job: {job_str}")

        mach_name = getattr(mach, "name", str(mach)) if (mach is not False and mach is not None) else "active-job"
        console = Console()
        with JobQueue.track(mach_name, job_str):
            while self._is_active_job(job_str, mach):
                with console.status(f"[bold yellow]Waiting for running job to finish: {job_str}...[/bold yellow]"):
                    time.sleep(10)

    def _preprocess(
        self,
        job_content: str,
        arch: str,
        os_name: str,
        worker: Any,
        mach: Any,
    ) -> str:
        job_content = job_content.replace("$ARCH", arch)
        job_content = job_content.replace("$OS", os_name)
        job_content = job_content.replace("$JOB", self.name)
        job_content = job_content.replace("$MAMA_HOST", getattr(config, "MAMA_HOST", ""))
        job_content = job_content.replace("$MAMA_PATH", getattr(config, "MAMA_PATH", ""))

        if worker is not False and worker is not None:
            worker_name = getattr(worker, "name", str(worker))
            job_content = job_content.replace("$WORKER", worker_name)

        if mach is not False and mach is not None:
            mach_name = getattr(mach, "name", str(mach))
            job_content = job_content.replace("$MACH", mach_name)

        args_list = self.args.split() if self.args else []

        for i, arg_val in enumerate(args_list, start=1):
            job_content = job_content.replace(f"$ARG{i}", arg_val)

        job_content = job_content.replace("$ARGS", self.args)

        return job_content

    def execute_prepare_job(self) -> bool:
        self.out(f"preparing job: {self.name} {self.arch} {self.os}")
        arch = self.arch
        os_name = self.os
        worker = self.mach

        prepare_file = (
            pathlib.Path(config.MAMA_PATH) / "jobs" / self.name / "prepare.job"
        )
        if not prepare_file.exists():
            self.fatal("Prepare job not found")

        try:
            job_content = prepare_file.read_text()
        except Exception as e:
            self.fatal(f"Prepare job not found or unreadable: {e}")

        job_str = f"prepare {self.name} {arch}/{os_name}"

        self._wait_for_active_job(job_str)

        worker_mach = select_machine(worker)
        if not worker_mach.is_idle():
            self.out(f"Waiting for job to finish: {worker_mach.job}")

        console = Console()
        with JobQueue.track(worker_mach.name, job_str):
            while not worker_mach.is_idle():
                with console.status(f"[bold yellow]Waiting for worker machine '{worker_mach.name}' job to finish: {worker_mach.job}...[/bold yellow]"):
                    time.sleep(10)
                    worker_mach.load()
                    worker_mach.detect_and_clear_stale_job()

        prev_job = worker_mach.job
        prev_job_pid = worker_mach.job_pid
        worker_mach.job = job_str
        worker_mach.job_pid = str(os.getpid())
        worker_mach.save()

        if Os.is_runnable(arch, os_name, worker_mach):
            worker_mach.set("os", Os(os_name, arch))

        job_content = self._preprocess(job_content, arch, os_name, worker, False)

        ret = self.execute(job_content, mach=False, worker=worker_mach)
        worker_mach.stop()

        worker_mach.load()
        worker_mach.job = prev_job
        worker_mach.job_pid = prev_job_pid
        worker_mach.save()

        if not ret:
            self.fatal("Prepare job FAILED")
        return ret

    def execute_run_job(self) -> Union[bool, Any]:
        self.out(f"running job: {self.name} {self.arch} {self.os}")
        mach_target = self.mach
        arch = self.arch
        os_name = self.os

        run_file = pathlib.Path(config.MAMA_PATH) / "jobs" / self.name / "run.job"
        if not run_file.exists():
            return False

        try:
            job_content = run_file.read_text()
        except Exception:
            return False

        mach = select_machine(mach_target)

        if not mach:
            self.error("Failed to find machine")
            return False

        if not mach.is_idle():
            self.out(f"Waiting for job to finish: {mach.job}")

        run_job_str = f"run {self.name} {arch}/{os_name}"
        console = Console()
        with JobQueue.track(mach.name, run_job_str):
            while not mach.is_idle():
                with console.status(f"[bold yellow]Waiting for target machine '{mach.name}' job to finish: {mach.job}...[/bold yellow]"):
                    time.sleep(10)
                    mach.load()
                    mach.detect_and_clear_stale_job()

        prev_job = mach.job
        prev_job_pid = mach.job_pid
        mach.job = run_job_str
        mach.job_pid = str(os.getpid())
        mach.save()

        if Os.is_runnable(arch, os_name, mach):
            mach.set("os", Os(os_name, arch))

        job_content = self._preprocess(job_content, arch, os_name, False, mach)

        ret = self.execute(job_content, mach=mach, worker=False)

        mach.stop()

        mach.load()
        mach.job = prev_job
        mach.job_pid = prev_job_pid
        mach.save()

        return ret

    def execute(self, job_content: str, mach: Any = False, worker: Any = False) -> bool:
        no_retry = self.no_retry

        ssh_ret = 0
        error_flag = False
        context = ""
        lines = job_content.splitlines()

        for line in lines:
            line = line.strip()

            if line.startswith("["):
                parts = line.split("[", 1)
                if len(parts) > 1:
                    context_inner = parts[1].split("]", 1)[0].strip()
                    old_context = context

                    if context_inner in ("MAMA", "DEVICE", "WORKER"):
                        context = context_inner

                    self.debug(f"Switching to context: {context}")

                    if old_context != context:
                        if old_context == "DEVICE" and mach:
                            if not mach.stop():
                                return False

                        if old_context == "WORKER" and worker:
                            if not worker.stop():
                                return False

                        m = None
                        if context == "DEVICE":
                            m = mach
                        elif context == "WORKER":
                            m = worker

                        if (context in ("DEVICE", "WORKER")) and m:
                            m.load()

                            if not m.stop():
                                return False

                            if m.only_vm:
                                if not m.start_vm():
                                    return False
                            else:
                                num_retries = 1 if no_retry else 3
                                start_success = False

                                for i in range(num_retries):
                                    if m.start():
                                        start_success = True
                                        break
                                    m.debug(f"Retrying to start machine: {i + 1}")
                                    time.sleep(2)

                                if not start_success:
                                    m.error("Failed to start machine")
                                    return False
                continue

            if line.startswith("#") or not line:
                continue

            if context == "MAMA":
                self.debug(f"(mama) {line}")
                logfile = getattr(Log, "logfile", False)
                if logfile:
                    with open(logfile, "a", encoding="utf-8") as lf:
                        res = subprocess.run(line, shell=True, stdout=lf, stderr=subprocess.STDOUT)
                else:
                    res = subprocess.run(line, shell=True)

                if res.returncode != 0:
                    self.error(f"EXECUTION FAILED: {line}")
                    error_flag = True
                    break

            line = line.replace("$RET", str(ssh_ret))

            mach_for_log = None
            res_code = 0

            if context == "DEVICE" and mach:
                res_code = mach.ssh_cmd(line, no_timeout=self.no_ssh_timeout, timeout_mins=self.timeout)
                mach_for_log = mach
            elif context == "WORKER" and worker:
                res_code = worker.ssh_cmd(line, no_timeout=self.no_ssh_timeout, timeout_mins=self.timeout)
                mach_for_log = worker

            if context in ("DEVICE", "WORKER"):
                ssh_ret = res_code if isinstance(res_code, int) else 1

                if res_code != 0:
                    if mach_for_log:
                        if res_code == 124:
                            mach_for_log.error(f"EXECUTION TIMEOUT: {line}")
                        else:
                            mach_for_log.error(f"EXECUTION FAILED: {line}")
                    error_flag = True
                    break

        self.out("Job finished")

        if mach:
            mach.stop()
        if worker:
            worker.stop()

        return not error_flag

    def out(self, msg: str, no_eol: bool = False, timestamp: bool = True) -> None:
        mach = select_machine(self.mach) if self.mach else False
        if not mach:
            out(msg, no_eol=no_eol, timestamp=timestamp)
        else:
            mach.out(msg, no_eol=no_eol, timestamp=timestamp)

    def debug(self, msg: str, no_eol: bool = False, timestamp: bool = True) -> None:
        mach = select_machine(self.mach) if self.mach else False
        if not mach:
            debug(msg, no_eol=no_eol, timestamp=timestamp)
        else:
            mach.debug(msg, no_eol=no_eol, timestamp=timestamp)

    def error(self, msg: str, no_eol: bool = False, timestamp: bool = True) -> None:
        mach = select_machine(self.mach) if self.mach else False
        if not mach:
            error(msg, no_eol=no_eol, timestamp=timestamp)
        else:
            mach.error(msg, no_eol=no_eol, timestamp=timestamp)

    def fatal(self, msg: str, errno: int = 1) -> None:
        mach = select_machine(self.mach) if self.mach else False
        if not mach:
            fatal(msg, errno=errno)
        else:
            mach.fatal(msg, errno=errno)