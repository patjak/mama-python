from __future__ import annotations

import datetime
import fcntl
import os
import pathlib
import subprocess
import sys
import traceback
from typing import Optional, Union

try:
    import zoneinfo
except ImportError:
    from backports import zoneinfo  # type: ignore

import config
from util import Util

LOG_LVL_FATAL = 0
LOG_LVL_ERROR = 1
LOG_LVL_DEFAULT = 2
LOG_LVL_DEBUG = 3
LOG_LVL_LOG = 4

_last_no_eol: bool = False


class Log:
    level: int = LOG_LVL_DEFAULT
    stdout = sys.stdout
    stderr = sys.stderr
    logfile: Union[str, bool] = False
    _pause: bool = False

    @classmethod
    def init(cls) -> None:
        if cls.stdout is None:
            cls.stdout = sys.stdout
        if cls.stderr is None:
            cls.stderr = sys.stderr

    @classmethod
    def pause(cls) -> None:
        cls._pause = True

    @classmethod
    def resume(cls) -> None:
        cls._pause = False

    @classmethod
    def set_level(cls, level: int) -> None:
        cls.level = level

    @classmethod
    def print_msg(cls, msg: str, level: int, timestamp: bool = True) -> None:
        try:
            tz = zoneinfo.ZoneInfo("Europe/Stockholm")
            now = datetime.datetime.now(tz)
        except Exception:
            now = datetime.datetime.now()

        debug_enabled = getattr(config, "DEBUG", False)

        if level == LOG_LVL_ERROR:
            cls.stderr.write(msg)
            cls.stderr.flush()
        elif level == LOG_LVL_DEFAULT or (level == LOG_LVL_DEBUG and debug_enabled):
            cls.stdout.write(msg)
            cls.stdout.flush()

        if cls.logfile and not cls._pause:
            date_str = f"[{now.strftime('%Y-%m-%d %H:%M:%S')}]" if timestamp else ""

            logfile_path = pathlib.Path(cls.logfile)
            if not logfile_path.exists():
                cmd = f"touch {logfile_path} && chgrp mama {logfile_path} && chmod 660 {logfile_path}"
                res = subprocess.run(cmd, shell=True)
                if res.returncode != 0:
                    fatal(f"Failed to create log file: {cls.logfile}")

            try:
                with open(logfile_path, "a") as stream:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                    stream.write(f"{date_str}\t{msg}")
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            except Exception as e:
                error(f"Failed to write to log ({cls.logfile}): {e}")

    @classmethod
    def set_file(cls, filename: Union[str, bool, None]) -> None:
        if not filename:
            cls.logfile = False
            return

        cls.logfile = str(pathlib.Path(config.MAMA_PATH) / "log" / str(filename))


def out(msg: str, no_eol: bool = False, timestamp: bool = True) -> int:
    global _last_no_eol

    if not no_eol:
        msg += "\n"

    debug_pid = getattr(config, "DEBUG_PID", False)
    if debug_pid and not _last_no_eol:
        msg = f"\r{Util.string_to_rand_color(str(os.getpid()))} {msg}"

    Log.print_msg(msg, LOG_LVL_DEFAULT, timestamp)
    _last_no_eol = no_eol

    return len(msg)


def error(msg: str, no_eol: bool = False, timestamp: bool = True) -> int:
    if not no_eol:
        msg += "\n"

    colored_msg = Util.string_to_color(msg, 0)
    Log.print_msg(colored_msg, LOG_LVL_ERROR, timestamp)

    return len(msg)


def fatal(msg: str, errno: int = 1) -> None:
    Log.print_msg(f"FATAL ERROR: {msg}\n", LOG_LVL_ERROR)
    traceback.print_stack()
    sys.exit(errno)


def debug(msg: str, no_eol: bool = False, timestamp: bool = False) -> int:
    if not no_eol:
        msg += "\n"

    Log.print_msg(msg, LOG_LVL_DEBUG, timestamp)

    return len(msg)