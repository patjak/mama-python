from __future__ import annotations

import contextlib
import fcntl
import os
import pathlib
import tempfile
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
        clean_os = os_name.replace("/", "_")
        lock_dir = pathlib.Path(config.MAMA_PATH) / "locks"
        try:
            lock_dir.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError):
            lock_dir = pathlib.Path(tempfile.gettempdir()) / "mama-locks"
            lock_dir.mkdir(parents=True, exist_ok=True)

        lock_file = lock_dir / f"os-{arch}-{clean_os}.lock"
        try:
            lock_file.touch(mode=0o664, exist_ok=True)
            if Util.is_root():
                lock_file.chmod(0o664)
        except OSError:
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

    @staticmethod
    def get_supported_archs() -> List[str]:
        """Return list of supported architectures, prioritizing host architecture and existing descriptions."""
        standard = ["x86_64", "aarch64", "ppc64le", "s390x", "i686", "riscv64"]
        try:
            host = os.uname().machine
        except Exception:
            host = "x86_64"
        res = [host] if host else []
        for a in Arch.get_buildable():
            if a not in res:
                res.append(a)
        for a in standard:
            if a not in res:
                res.append(a)
        return res