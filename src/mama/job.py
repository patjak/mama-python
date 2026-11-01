from __future__ import annotations

import os
import pathlib
import shlex
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple, Union

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

    def get_env(
        self,
        arch: str = "",
        os_name: str = "",
        worker: Any = None,
        mach: Any = None,
    ) -> Dict[str, str]:
        """Build dictionary of standard MAMA environment variables."""
        env: Dict[str, str] = {
            "ARCH": str(arch or getattr(self, "arch", "")),
            "OS": str(os_name or getattr(self, "os", "")),
            "JOB": str(self.name),
            "MAMA_HOST": str(getattr(config, "MAMA_HOST", "")),
            "MAMA_PATH": str(getattr(config, "MAMA_PATH", "")),
            "ARGS": str(self.args or ""),
        }

        if worker is not False and worker is not None:
            env["WORKER"] = getattr(worker, "name", str(worker))
        if mach is not False and mach is not None:
            env["MACH"] = getattr(mach, "name", str(mach))

        args_list = self.args.split() if self.args else []
        for i, arg_val in enumerate(args_list, start=1):
            env[f"ARG{i}"] = arg_val

        return env

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
        if not isinstance(worker_mach, Machine):
            self.fatal(f"Worker machine '{worker}' not found")

        pid = os.getpid()
        JobQueue.enqueue(worker_mach.name, job_str, pid)
        console = Console()
        try:
            while not JobQueue.try_claim_machine(worker_mach.name, job_str, pid):
                worker_mach.load()
                with console.status(
                    f"[bold yellow]Waiting for worker machine '{worker_mach.name}' in queue: {worker_mach.job}...[/bold yellow]"
                ):
                    time.sleep(5)

            worker_mach.load()

            if Os.is_runnable(arch, os_name, worker_mach):
                worker_mach.set("os", Os(os_name, arch))

            job_content = self._preprocess(job_content, arch, os_name, worker, False)
            env = self.get_env(arch, os_name, worker=worker_mach, mach=False)

            ret = self.execute(job_content, mach=False, worker=worker_mach, env=env)
            if not ret:
                self.error("Prepare job FAILED")
            return ret
        except KeyboardInterrupt:
            self.error(f"\nPrepare job '{self.name}' interrupted by user (Ctrl+C). Cleaning up...")
            return False
        finally:
            JobQueue.dequeue(worker_mach.name, pid)
            worker_mach.load()
            if str(worker_mach.job_pid) == str(pid):
                worker_mach.job = ""
                worker_mach.job_pid = ""
                worker_mach.save()
            try:
                worker_mach.stop()
            except Exception:
                pass

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
        if not isinstance(mach, Machine):
            self.error("Failed to find machine")
            return False

        run_job_str = f"run {self.name} {arch}/{os_name}"
        pid = os.getpid()
        JobQueue.enqueue(mach.name, run_job_str, pid)
        console = Console()
        try:
            while not JobQueue.try_claim_machine(mach.name, run_job_str, pid):
                mach.load()
                with console.status(
                    f"[bold yellow]Waiting for target machine '{mach.name}' in queue: {mach.job}...[/bold yellow]"
                ):
                    time.sleep(5)

            mach.load()

            if Os.is_runnable(arch, os_name, mach):
                mach.set("os", Os(os_name, arch))

            job_content = self._preprocess(job_content, arch, os_name, False, mach)
            env = self.get_env(arch, os_name, worker=False, mach=mach)

            ret = self.execute(job_content, mach=mach, worker=False, env=env)
            return ret
        except KeyboardInterrupt:
            self.error(f"\nRun job '{self.name}' interrupted by user (Ctrl+C). Cleaning up...")
            return False
        finally:
            JobQueue.dequeue(mach.name, pid)
            mach.load()
            if str(mach.job_pid) == str(pid):
                mach.job = ""
                mach.job_pid = ""
                mach.save()
            try:
                mach.stop()
            except Exception:
                pass

    @staticmethod
    def parse_sections(job_content: str) -> List[Tuple[str, List[str]]]:
        """Parse raw job script content into structured (context, [lines]) blocks."""
        sections: List[Tuple[str, List[str]]] = []
        current_context = "MAMA"
        current_lines: List[str] = []

        for raw_line in job_content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            if line.startswith("["):
                parts = line.split("[", 1)
                if len(parts) > 1:
                    ctx = parts[1].split("]", 1)[0].strip()
                    if ctx in ("MAMA", "DEVICE", "WORKER"):
                        if current_lines:
                            sections.append((current_context, current_lines))
                            current_lines = []
                        current_context = ctx
                        continue

            current_lines.append(line)

        if current_lines:
            sections.append((current_context, current_lines))

        return sections

    def execute(
        self,
        job_content: str,
        mach: Any = False,
        worker: Any = False,
        env: Optional[Dict[str, str]] = None,
    ) -> bool:
        no_retry = self.no_retry
        sections = self.parse_sections(job_content)

        if env is None:
            env = self.get_env(worker=worker, mach=mach)

        old_target: Any = None

        for context, lines in sections:
            if not lines:
                continue

            target_mach = mach if context == "DEVICE" else (worker if context == "WORKER" else None)

            # If transitioning away from an active target machine to MAMA, stop it
            if old_target and old_target != target_mach:
                self.debug(f"Stopping previous machine '{old_target.name}' before context switch to {context}")
                old_target.stop()
                old_target = None

            # If entering a machine context (DEVICE or WORKER), ensure it is started
            if target_mach:
                target_mach.load()
                if not target_mach.is_started:
                    self.debug(f"Starting {target_mach.name} for {context} execution")
                    if target_mach.only_vm:
                        if not target_mach.start_vm():
                            return False
                    else:
                        num_retries = 1 if no_retry else 3
                        start_success = False
                        for i in range(num_retries):
                            if target_mach.start():
                                start_success = True
                                break
                            target_mach.debug(f"Retrying to start machine: {i + 1}")
                            time.sleep(2)

                        if not start_success:
                            target_mach.error(f"Failed to start machine '{target_mach.name}'")
                            return False

                old_target = target_mach

            # Execute section
            if context == "MAMA":
                local_env = {**os.environ, **env}
                for line in lines:
                    self.debug(f"(mama) {line}")
                    logfile = getattr(Log, "logfile", False)
                    if logfile:
                        with open(logfile, "a", encoding="utf-8") as lf:
                            res = subprocess.run(line, shell=True, env=local_env, stdout=lf, stderr=subprocess.STDOUT)
                    else:
                        res = subprocess.run(line, shell=True, env=local_env)

                    if res.returncode != 0:
                        self.error(f"EXECUTION FAILED: {line}")
                        if mach:
                            mach.stop()
                        if worker:
                            worker.stop()
                        return False

            elif context in ("DEVICE", "WORKER") and target_mach:
                self.debug(f"({context}) Streaming {len(lines)} command(s) to {target_mach.name} via SSH subshell")
                exports = "\n".join(f"export {k}={shlex.quote(v)}" for k, v in env.items()) + "\n"
                script_body = exports + "\n".join(lines) + "\n"
                res_code = target_mach.ssh_script(
                    script=script_body,
                    no_timeout=self.no_ssh_timeout,
                    timeout_mins=self.timeout,
                )
                if res_code != 0:
                    if res_code == 124:
                        target_mach.error(f"EXECUTION TIMEOUT in {context} script block")
                    else:
                        target_mach.error(f"EXECUTION FAILED in {context} script block (exit code {res_code})")
                    if mach:
                        mach.stop()
                    if worker:
                        worker.stop()
                    return False

        self.out("Job finished")

        if mach:
            mach.stop()
        if worker:
            worker.stop()

        return True

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