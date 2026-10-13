from __future__ import annotations

import datetime
import pathlib
import sqlite3
import time
from typing import Any, Dict, List, Optional

import config


class MachineRepository:
    @classmethod
    def _get_db_path(cls) -> pathlib.Path:
        db_path_str = getattr(config, "DB_FILE", f"{getattr(config, 'MAMA_PATH', '/usr/share/mama')}/mama.db")
        return pathlib.Path(db_path_str)

    @classmethod
    def _get_connection(cls) -> sqlite3.Connection:
        db_path = cls._get_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
        return conn

    @classmethod
    def init_db(cls) -> None:
        """Initialize tables, migrate columns if necessary, and ensure indexes exist."""
        with cls._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS machines (
                    name TEXT PRIMARY KEY,
                    mac TEXT UNIQUE NOT NULL,
                    ip TEXT DEFAULT '',
                    arch TEXT DEFAULT '',
                    os TEXT DEFAULT '',
                    kernel TEXT DEFAULT '',
                    reservation TEXT DEFAULT '',
                    resources TEXT DEFAULT '',
                    boot_params TEXT DEFAULT '',
                    vm_params TEXT DEFAULT '',
                    only_vm INTEGER DEFAULT 0,
                    job TEXT DEFAULT '',
                    job_pid TEXT DEFAULT '',
                    startcmd TEXT DEFAULT '',
                    stopcmd TEXT DEFAULT '',
                    powercmd TEXT DEFAULT '',
                    start_timeout INTEGER DEFAULT 240,
                    _is_started INTEGER DEFAULT 0
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_machines_mac ON machines(mac);")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS boot_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    machine_name TEXT NOT NULL,
                    os TEXT NOT NULL DEFAULT '',
                    boot_type TEXT NOT NULL,
                    duration INTEGER NOT NULL,
                    timestamp INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT ''
                );
            """)

            # Defensive schema migration in case boot_history previously existed without os or created_at
            columns_info = conn.execute("PRAGMA table_info(boot_history);").fetchall()
            col_names = [col["name"] for col in columns_info]
            if "os" not in col_names:
                conn.execute("ALTER TABLE boot_history ADD COLUMN os TEXT NOT NULL DEFAULT '';")
            if "created_at" not in col_names:
                conn.execute("ALTER TABLE boot_history ADD COLUMN created_at TEXT NOT NULL DEFAULT '';")

            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_boot_history_lookup ON boot_history(machine_name, os, boot_type);"
            )
            conn.commit()

    @classmethod
    def load(cls) -> bool:
        """Initialize DB connection and verify table readiness."""
        try:
            cls.init_db()
            return True
        except Exception:
            return False

    @classmethod
    def save(cls) -> bool:
        """No-op maintained for backward compatibility."""
        return True

    @classmethod
    def get_by_name(cls, name: str) -> Optional[dict]:
        """Direct point-lookup by machine name."""
        with cls._get_connection() as conn:
            row = conn.execute("SELECT * FROM machines WHERE name = ?", (name,)).fetchone()
            return dict(row) if row else None

    @classmethod
    def get_by_mac(cls, mac: str) -> Optional[dict]:
        """Direct point-lookup by MAC address."""
        with cls._get_connection() as conn:
            row = conn.execute("SELECT * FROM machines WHERE LOWER(mac) = LOWER(?)", (mac,)).fetchone()
            return dict(row) if row else None

    @classmethod
    def get_all(cls) -> List[dict]:
        """Fetch all machine rows ordered by name."""
        with cls._get_connection() as conn:
            rows = conn.execute("SELECT * FROM machines ORDER BY name ASC").fetchall()
            return [dict(r) for r in rows]

    @classmethod
    def update_fields(cls, name: str, fields: Dict[str, Any]) -> None:
        """Atomically update specified fields for a machine."""
        if not fields:
            return

        set_clause = ", ".join([f"{col} = ?" for col in fields.keys()])
        values = list(fields.values()) + [name]

        with cls._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(f"UPDATE machines SET {set_clause} WHERE name = ?", values)
            conn.commit()

    @classmethod
    def upsert_machine(cls, m_dict: Dict[str, Any]) -> None:
        """Full upsert for adding or completely updating a machine."""
        columns = [
            "name", "mac", "ip", "arch", "os", "kernel",
            "reservation", "resources", "boot_params", "vm_params",
            "only_vm", "job", "job_pid", "startcmd", "stopcmd", "powercmd",
            "start_timeout", "_is_started"
        ]
        vals = [str(m_dict.get(col, "") or "") for col in columns]
        placeholders = ", ".join(["?"] * len(columns))
        col_names = ", ".join(columns)
        update_assignments = ", ".join([f"{col}=EXCLUDED.{col}" for col in columns if col != "name"])

        sql = f"""
            INSERT INTO machines ({col_names})
            VALUES ({placeholders})
            ON CONFLICT(name) DO UPDATE SET {update_assignments};
        """
        with cls._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(sql, vals)
            conn.commit()

    @classmethod
    def update_machine(cls, machine: Any) -> None:
        """Compatibility wrapper for updating a machine object or dict."""
        if hasattr(machine, "to_dict"):
            m_dict = machine.to_dict()
        elif isinstance(machine, dict):
            m_dict = machine
        else:
            attrs = [
                "name", "mac", "ip", "arch", "os", "kernel",
                "reservation", "resources", "boot_params", "vm_params",
                "only_vm", "job", "job_pid", "startcmd", "stopcmd", "powercmd",
                "start_timeout"
            ]
            m_dict = {attr: getattr(machine, attr, "") for attr in attrs}
            m_dict["_is_started"] = getattr(machine, "_is_started", "0")

        cls.upsert_machine(m_dict)

    @classmethod
    def add_machine(cls, machine: Any) -> None:
        cls.update_machine(machine)

    @classmethod
    def delete_machine(cls, machine: Any) -> None:
        """Delete machine record by name."""
        name = getattr(machine, "name", str(machine))
        with cls._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute("DELETE FROM machines WHERE name = ?", (name,))
            conn.commit()

    @classmethod
    def record_boot_time(cls, machine_name: str, os_name: str, boot_type: str, duration: int) -> None:
        """Record a successful boot duration and prune records older than the latest 10 samples for this target."""
        clean_os = os_name.strip() if os_name else "default"
        now_epoch = int(time.time())
        created_at_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with cls._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                """
                INSERT INTO boot_history (machine_name, os, boot_type, duration, timestamp, created_at)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (machine_name, clean_os, boot_type, int(duration), now_epoch, created_at_str),
            )
            # Prune to maintain only the 10 most recent entries per (machine_name, os, boot_type) tuple
            conn.execute(
                """
                DELETE FROM boot_history
                WHERE machine_name = ? AND os = ? AND boot_type = ? AND id NOT IN (
                    SELECT id FROM boot_history
                    WHERE machine_name = ? AND os = ? AND boot_type = ?
                    ORDER BY timestamp DESC LIMIT 10
                );
                """,
                (machine_name, clean_os, boot_type, machine_name, clean_os, boot_type),
            )
            conn.commit()

    @classmethod
    def get_calculated_timeout(
        cls, machine_name: str, os_name: str, boot_type: str, default_timeout: int = 240
    ) -> int:
        """Calculate adaptive boot timeout based on rolling average multiplied by 1.25x."""
        clean_os = os_name.strip() if os_name else "default"
        with cls._get_connection() as conn:
            row = conn.execute(
                """
                SELECT AVG(duration) as avg_duration, COUNT(*) as sample_count
                FROM boot_history
                WHERE machine_name = ? AND os = ? AND boot_type = ?;
                """,
                (machine_name, clean_os, boot_type),
            ).fetchone()

            if row and row["sample_count"] is not None and row["sample_count"] >= 3:
                avg_duration = float(row["avg_duration"])
                calculated = int(avg_duration * 1.25)
                return max(45, calculated)

        return default_timeout

    @classmethod
    def get_boot_history(cls, machine_name: Optional[str] = None) -> List[dict]:
        """Query boot history rows ordered chronologically descending."""
        with cls._get_connection() as conn:
            if machine_name:
                rows = conn.execute(
                    """
                    SELECT * FROM boot_history
                    WHERE machine_name = ?
                    ORDER BY timestamp DESC;
                    """,
                    (machine_name,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM boot_history
                    ORDER BY timestamp DESC;
                    """
                ).fetchall()
            return [dict(r) for r in rows]


# Module-level locking functions expected by job.py, machine.py, etc.
def LOCK() -> None:
    pass


def UNLOCK() -> None:
    pass


def IS_LOCKED() -> bool:
    return True


def SLEEP_ON_LOCK(seconds: int) -> None:
    time.sleep(seconds)


def select_machine(*args: Any, **kwargs: Any) -> Any:
    """Lazy wrapper to import select_machine from main at call time."""
    from main import select_machine as _select_machine
    return _select_machine(*args, **kwargs)


# Dynamic attribute resolver for module re-exports
def __getattr__(name: str) -> Any:
    if name == "Util":
        from util import Util
        return Util
    if name == "Os":
        from os_mgr import Os
        return Os
    if name == "Machine":
        from machine import Machine
        return Machine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")