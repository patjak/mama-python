from __future__ import annotations

import contextlib
import fcntl
import os
import pathlib
from typing import Any, List, Optional

# Environment & helper stubs
from . import config
from .util import Util


class Os:
    def __init__(self, os_name: str = "", arch: str = "") -> None:
        self.os: str = os_name
        self.arch: str = arch

    @staticmethod
    @contextlib.contextmanager
    def lock(arch: str, os_name: str, shared: bool = False):
        """Advisory filesystem lock for an OS template."""
        lock_dir = pathlib.Path(config.MAMA_PATH) / "os" / arch
        try:
            lock_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        lock_file = lock_dir / f".{os_name}.lock"
        try:
            if not lock_file.exists():
                lock_file.touch(mode=0o666, exist_ok=True)
        except Exception:
            pass

        operation = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        with open(lock_file, "a+") as f:
            fcntl.flock(f.fileno(), operation)
            try:
                yield
            finally:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass

    @staticmethod
    def is_buildable(arch: str, os_name: str) -> bool:
        path = str(pathlib.Path(config.MAMA_PATH) / "os-descriptions")
        oses = Util.get_directory_contents(path, 2)
        return f"{arch}/{os_name}" in oses

    @staticmethod
    def is_installable(arch: str, os_name: str, mach: Optional[Any] = None) -> bool:
        path = str(pathlib.Path(config.MAMA_PATH) / "os")
        oses = Util.get_directory_contents(path, 2)
        return f"{arch}/{os_name}" in oses

    @staticmethod
    def is_runnable(arch: str, os_name: str, mach: Any) -> bool:
        mach_name = getattr(mach, "name", str(mach))
        path = str(pathlib.Path(config.MAMA_PATH) / "machines" / mach_name)
        oses = Util.get_directory_contents(path, 2)
        return f"{arch}/{os_name}" in oses


class Arch:
    @staticmethod
    def is_buildable(arch: str) -> bool:
        path = str(pathlib.Path(config.MAMA_PATH) / "os-descriptions")
        archs = Util.get_directory_contents(path, 1)
        return arch in archs

    @staticmethod
    def is_installable(arch: str, mach: Optional[Any] = None) -> bool:
        path = str(pathlib.Path(config.MAMA_PATH) / "os")
        archs = Util.get_directory_contents(path, 1)
        return arch in archs

    @staticmethod
    def is_runnable(arch: str, mach: Any) -> bool:
        mach_name = getattr(mach, "name", str(mach))
        path = str(pathlib.Path(config.MAMA_PATH) / "machines" / mach_name)
        archs = Util.get_directory_contents(path, 1)
        return arch in archs

    @staticmethod
    def get_buildable() -> List[str]:
        archs: List[str] = []
        path = str(pathlib.Path(config.MAMA_PATH) / "os-descriptions")
        oses = Util.get_directory_contents(path, 2)

        for os_entry in oses:
            arch = os_entry.split("/")[0]
            if arch not in archs:
                archs.append(arch)

        return archs