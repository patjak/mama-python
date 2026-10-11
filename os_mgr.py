from __future__ import annotations

import pathlib
from typing import Any, List, Optional

# Environment & helper stubs
import config
from repository import Util


class Os:
    def __init__(self, os_name: str = "", arch: str = "") -> None:
        self.os: str = os_name
        self.arch: str = arch

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