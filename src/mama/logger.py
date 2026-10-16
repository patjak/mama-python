from __future__ import annotations

import logging
import os
import pathlib
import sys
import traceback
from typing import Optional, Union

import config
from util import Util

LOG_LVL_FATAL = logging.CRITICAL
LOG_LVL_ERROR = logging.ERROR
LOG_LVL_DEFAULT = logging.INFO
LOG_LVL_DEBUG = logging.DEBUG
LOG_LVL_LOG = logging.INFO

_last_no_eol: bool = False


class MamaConsoleHandler(logging.Handler):
    """Console handler routing stdout/stderr with optional EOL omission."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            no_eol = getattr(record, "no_eol", False)
            term = "" if no_eol else "\n"
            stream = sys.stderr if record.levelno >= logging.ERROR else sys.stdout
            stream.write(msg + term)
            stream.flush()
        except Exception:
            self.handleError(record)


class MamaFileFormatter(logging.Formatter):
    """File formatter matching MAMA tab-delimited timestamp syntax."""

    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        if getattr(record, "no_timestamp", False):
            return msg
        ts = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        return f"[{ts}]\t{msg}"


class MamaFileHandler(logging.FileHandler):
    """FileHandler supporting pause states and raw trailing newlines."""

    def emit(self, record: logging.LogRecord) -> None:
        if Log._pause:
            return
        no_eol = getattr(record, "no_eol", False)
        orig_terminator = self.terminator
        if no_eol:
            self.terminator = ""
        try:
            super().emit(record)
        finally:
            self.terminator = orig_terminator


# Root MAMA Logger
logger = logging.getLogger("mama")
logger.setLevel(logging.DEBUG)
logger.propagate = False

# Console Handler
_console_handler = MamaConsoleHandler()
_console_handler.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(_console_handler)


class Log:
    level: int = logging.INFO
    stdout = sys.stdout
    stderr = sys.stderr
    logfile: Union[str, bool] = False
    _pause: bool = False
    _file_handler: Optional[MamaFileHandler] = None

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
        _console_handler.setLevel(level)

    @classmethod
    def set_file(cls, filename: Union[str, bool, None]) -> None:
        if cls._file_handler:
            logger.removeHandler(cls._file_handler)
            cls._file_handler.close()
            cls._file_handler = None

        if not filename:
            cls.logfile = False
            return

        log_path = pathlib.Path(config.MAMA_PATH) / "log" / str(filename)
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handler = MamaFileHandler(str(log_path), mode="a", encoding="utf-8")
            handler.setFormatter(MamaFileFormatter())
            handler.setLevel(logging.DEBUG)
            logger.addHandler(handler)
            cls._file_handler = handler
            cls.logfile = str(log_path)
        except Exception as e:
            cls.logfile = False
            error(f"Failed to initialize file logger at {log_path}: {e}")


def out(msg: str, no_eol: bool = False, timestamp: bool = True) -> int:
    global _last_no_eol

    debug_pid = getattr(config, "DEBUG_PID", False)
    display_msg = msg
    if debug_pid and not _last_no_eol:
        display_msg = f"\r{Util.string_to_rand_color(str(os.getpid()))} {msg}"

    extra = {
        "no_eol": no_eol,
        "no_timestamp": not timestamp,
    }
    logger.info(display_msg, extra=extra)
    _last_no_eol = no_eol
    return len(msg)


def error(msg: str, no_eol: bool = False, timestamp: bool = True) -> int:
    colored_msg = Util.string_to_color(msg, 0)
    extra = {
        "no_eol": no_eol,
        "no_timestamp": not timestamp,
    }
    logger.error(colored_msg, extra=extra)
    return len(msg)


def fatal(msg: str, errno: int = 1) -> None:
    logger.critical(f"FATAL ERROR: {msg}")
    traceback.print_stack()
    sys.exit(errno)


def debug(msg: str, no_eol: bool = False, timestamp: bool = False) -> int:
    if getattr(config, "DEBUG", False):
        extra = {
            "no_eol": no_eol,
            "no_timestamp": not timestamp,
        }
        logger.debug(msg, extra=extra)
    return len(msg)