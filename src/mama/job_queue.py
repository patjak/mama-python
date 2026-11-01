from __future__ import annotations

import contextlib
import os
import time
from typing import Any, Dict, List, Optional

from . import config
from .logger import debug
from .repository import MachineRepository


class JobQueue:
    """Tracks waiting processes in SQLite queue table across concurrent executions."""

    @classmethod
    def _init_db(cls) -> None:
        try:
            with MachineRepository.connection() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS job_queue (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        machine_name TEXT NOT NULL,
                        job TEXT NOT NULL,
                        pid TEXT NOT NULL,
                        queued_at INTEGER NOT NULL
                    );
                    """
                )
                conn.commit()
        except Exception as e:
            debug(f"[JobQueue] Failed to initialize table: {e}")

    @classmethod
    def enqueue(cls, machine_name: str, job_str: str, pid: Optional[int] = None) -> None:
        cls._init_db()
        if pid is None:
            pid = os.getpid()
        now = int(time.time())
        try:
            with MachineRepository.connection() as conn:
                conn.execute(
                    "INSERT INTO job_queue (machine_name, job, pid, queued_at) VALUES (?, ?, ?, ?)",
                    (machine_name, job_str, str(pid), now),
                )
                conn.commit()
        except Exception as e:
            debug(f"[JobQueue] Error enqueuing {job_str} on {machine_name}: {e}")

    @classmethod
    def dequeue(cls, machine_name: str, pid: Optional[int] = None) -> None:
        cls._init_db()
        if pid is None:
            pid = os.getpid()
        try:
            with MachineRepository.connection() as conn:
                conn.execute(
                    "DELETE FROM job_queue WHERE machine_name = ? AND pid = ?",
                    (machine_name, str(pid)),
                )
                conn.commit()
        except Exception as e:
            debug(f"[JobQueue] Error dequeuing PID {pid} on {machine_name}: {e}")

    @classmethod
    def clear_machine(cls, machine_name: str) -> None:
        cls._init_db()
        try:
            with MachineRepository.connection() as conn:
                if machine_name and machine_name != "all":
                    conn.execute("DELETE FROM job_queue WHERE machine_name = ?", (machine_name,))
                else:
                    conn.execute("DELETE FROM job_queue")
                conn.commit()
        except Exception as e:
            debug(f"[JobQueue] Error clearing queue for {machine_name}: {e}")

    @staticmethod
    def is_pid_alive(pid: Any) -> bool:
        """Check if process ID is actively running."""
        try:
            pid_int = int(pid)
        except (ValueError, TypeError):
            return False
        if pid_int <= 0:
            return False
        try:
            os.kill(pid_int, 0)
            return True
        except PermissionError:
            return True
        except (ProcessLookupError, OSError):
            return False

    @classmethod
    def is_head_of_queue(cls, machine_name: str, pid: Optional[int] = None) -> bool:
        """Check if the given PID is the earliest active queued entry for this machine."""
        if pid is None:
            pid = os.getpid()
        queue = cls.get_queue(machine_name)
        if not queue:
            return True
        return str(queue[0].get("pid")) == str(pid)

    @classmethod
    def try_claim_machine(cls, machine_name: str, job_str: str, pid: Optional[int] = None) -> bool:
        """Atomically claim the machine for this PID if the machine is idle AND this PID is at the head of the queue.

        If successful, sets machine.job = job_str, machine.job_pid = str(pid), and dequeues this PID from job_queue.
        Returns True if claimed, False otherwise.
        """
        cls._init_db()
        if pid is None:
            pid = os.getpid()

        try:
            with MachineRepository.connection() as conn:
                conn.execute("BEGIN IMMEDIATE;")

                # 1. Clean stale entries in queue and fetch active PIDs
                rows = conn.execute(
                    "SELECT * FROM job_queue WHERE machine_name = ? ORDER BY queued_at ASC, id ASC",
                    (machine_name,),
                ).fetchall()

                active_pids: List[str] = []
                for r in rows:
                    p = str(r["pid"])
                    if cls.is_pid_alive(p):
                        active_pids.append(p)
                    else:
                        conn.execute("DELETE FROM job_queue WHERE machine_name = ? AND pid = ?", (machine_name, p))

                # If this PID is not in the queue or not at the head:
                if active_pids and str(active_pids[0]) != str(pid):
                    conn.commit()
                    return False

                # 2. Check if machine is idle in the database
                mach_row = conn.execute(
                    "SELECT job, job_pid FROM machines WHERE name = ?",
                    (machine_name,),
                ).fetchone()
                if not mach_row:
                    conn.commit()
                    return False

                curr_job = mach_row["job"] or ""
                curr_job_pid = mach_row["job_pid"] or ""
                if curr_job and curr_job_pid and cls.is_pid_alive(curr_job_pid):
                    # Machine is still busy with a living job
                    conn.commit()
                    return False

                # 3. Machine is idle and we are at head of queue: Claim it atomically!
                conn.execute(
                    "UPDATE machines SET job = ?, job_pid = ? WHERE name = ?",
                    (job_str, str(pid), machine_name),
                )
                conn.execute(
                    "DELETE FROM job_queue WHERE machine_name = ? AND pid = ?",
                    (machine_name, str(pid)),
                )
                conn.commit()
                return True
        except Exception as e:
            debug(f"[JobQueue] Error in try_claim_machine: {e}")
            return False

    @classmethod
    def get_queue(cls, machine_name: str = "") -> List[Dict[str, Any]]:
        cls._init_db()
        try:
            with MachineRepository.connection() as conn:
                query = "SELECT * FROM job_queue"
                params: List[Any] = []
                if machine_name:
                    query += " WHERE machine_name = ?"
                    params.append(machine_name)
                query += " ORDER BY queued_at ASC, id ASC"
                rows = conn.execute(query, params).fetchall()
        except Exception as e:
            debug(f"[JobQueue] Error fetching queue: {e}")
            return []

        valid_entries: List[Dict[str, Any]] = []
        for r in rows:
            entry = dict(r)
            try:
                pid = int(entry["pid"])
            except (ValueError, TypeError):
                pid = 0

            if pid <= 0:
                cls.dequeue(entry["machine_name"], pid)
                continue

            try:
                os.kill(pid, 0)
                valid_entries.append(entry)
            except PermissionError:
                valid_entries.append(entry)
            except (ProcessLookupError, OSError):
                cls.dequeue(entry["machine_name"], pid)

        return valid_entries

    @classmethod
    def get_queue_counts(cls) -> Dict[str, int]:
        """Return a mapping of machine_name to the number of active queued jobs."""
        entries = cls.get_queue()
        counts: Dict[str, int] = {}
        for entry in entries:
            m_name = entry["machine_name"]
            counts[m_name] = counts.get(m_name, 0) + 1
        return counts

    @classmethod
    def get_queue_count(cls, machine_name: str) -> int:
        """Return the number of active queued jobs for a specific machine."""
        return len(cls.get_queue(machine_name))

    @classmethod
    @contextlib.contextmanager
    def track(cls, machine_name: str, job_str: str):
        pid = os.getpid()
        cls.enqueue(machine_name, job_str, pid)
        try:
            yield
        finally:
            cls.dequeue(machine_name, pid)