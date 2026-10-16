from __future__ import annotations

import contextlib
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

import config
from logger import debug


class JobQueue:
    """Tracks waiting processes in SQLite queue table across concurrent executions."""

    @staticmethod
    def _get_db_path() -> str:
        return getattr(
            config,
            "DB_FILE",
            f"{getattr(config, 'MAMA_PATH', '/usr/share/mama')}/mama.db",
        )

    @classmethod
    def _init_db(cls) -> None:
        db_path = cls._get_db_path()
        try:
            with sqlite3.connect(db_path, timeout=10) as conn:
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
        db_path = cls._get_db_path()
        try:
            with sqlite3.connect(db_path, timeout=10) as conn:
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
        db_path = cls._get_db_path()
        try:
            with sqlite3.connect(db_path, timeout=10) as conn:
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
        db_path = cls._get_db_path()
        try:
            with sqlite3.connect(db_path, timeout=10) as conn:
                if machine_name and machine_name != "all":
                    conn.execute("DELETE FROM job_queue WHERE machine_name = ?", (machine_name,))
                else:
                    conn.execute("DELETE FROM job_queue")
                conn.commit()
        except Exception as e:
            debug(f"[JobQueue] Error clearing queue for {machine_name}: {e}")

    @classmethod
    def get_queue(cls, machine_name: str = "") -> List[Dict[str, Any]]:
        cls._init_db()
        db_path = cls._get_db_path()
        try:
            with sqlite3.connect(db_path, timeout=10) as conn:
                conn.row_factory = sqlite3.Row
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
    @contextlib.contextmanager
    def track(cls, machine_name: str, job_str: str):
        pid = os.getpid()
        cls.enqueue(machine_name, job_str, pid)
        try:
            yield
        finally:
            cls.dequeue(machine_name, pid)