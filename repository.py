from __future__ import annotations

import fcntl
import html
import os
import pathlib
import threading
import time
import xml.dom.minidom
import xml.etree.ElementTree as ET
from typing import Any, List, Optional

import config
from util import Util
from os_mgr import Os


def sanitize_text(text: Optional[str]) -> str:
    if not text:
        return ""
    prev = None
    curr = str(text)
    while curr != prev:
        prev = curr
        curr = html.unescape(curr)
    return curr


class MachineRepository:
    settings: Optional[ET.Element] = None
    _lock_stream: Optional[Any] = None
    _lock_count: int = 0
    _rlock = threading.RLock()

    @classmethod
    def lock(cls) -> bool:
        with cls._rlock:
            if cls._lock_count == 0:
                settings_path = pathlib.Path(config.SETTINGS_FILE)
                if not settings_path.exists():
                    return False
                try:
                    cls._lock_stream = open(settings_path, "r+")
                    fcntl.flock(cls._lock_stream.fileno(), fcntl.LOCK_EX)
                except Exception:
                    return False
            cls._lock_count += 1
            return True

    @classmethod
    def unlock(cls) -> None:
        with cls._rlock:
            if cls._lock_count > 0:
                cls._lock_count -= 1
                if cls._lock_count == 0 and cls._lock_stream is not None:
                    try:
                        fcntl.flock(cls._lock_stream.fileno(), fcntl.LOCK_UN)
                        cls._lock_stream.close()
                    finally:
                        cls._lock_stream = None

    @classmethod
    def is_lock_held(cls) -> bool:
        with cls._rlock:
            return cls._lock_count > 0

    @classmethod
    def load(cls) -> bool:
        settings_path = pathlib.Path(config.SETTINGS_FILE)
        if not settings_path.exists():
            return False

        try:
            tree = ET.parse(settings_path)
            root = tree.getroot()

            for elem in root.iter():
                if elem.text:
                    elem.text = sanitize_text(elem.text)

            cls.settings = root
            return True
        except Exception:
            return False

    @classmethod
    def save(cls) -> bool:
        if cls.settings is None:
            return False

        settings_path = pathlib.Path(config.SETTINGS_FILE)

        for elem in cls.settings.iter():
            if elem.text:
                elem.text = sanitize_text(elem.text)

        try:
            raw_xml = ET.tostring(cls.settings, encoding="utf-8")
            dom = xml.dom.minidom.parseString(raw_xml)
            
            pretty_xml = dom.toprettyxml(indent="  ")
            lines = [line for line in pretty_xml.splitlines() if line.strip()]
            formatted_xml = "\n".join(lines) + "\n"

            with open(settings_path, "w", encoding="utf-8") as f:
                f.write(formatted_xml)
            return True
        except Exception:
            return False

    @classmethod
    def update_machine(cls, mach: Any) -> bool:
        cls.load()
        if cls.settings is None:
            return False

        target_elem = None
        for elem in cls.settings.findall("machine"):
            mac_elem = elem.find("mac")
            name_elem = elem.find("name")
            mac_val = mac_elem.text if mac_elem is not None and mac_elem.text else ""
            name_val = name_elem.text if name_elem is not None and name_elem.text else ""

            if (mach.mac and mac_val.lower() == mach.mac.lower()) or (mach.name and name_val == mach.name):
                target_elem = elem
                break

        if target_elem is None:
            return cls.add_machine(mach)

        fields = [
            ("name", mach.name),
            ("mac", mach.mac),
            ("ip", getattr(mach, "ip", "")),
            ("is_started", str(getattr(mach, "_is_started", "0"))),
            ("only_vm", str(getattr(mach, "only_vm", "0"))),
            ("arch", getattr(mach, "arch", "")),
            ("os", getattr(mach, "os", "")),
            ("kernel", getattr(mach, "kernel", "")),
            ("reservation", getattr(mach, "reservation", "")),
            ("resources", getattr(mach, "resources", "")),
            ("boot_params", getattr(mach, "boot_params", "")),
            ("vm_params", getattr(mach, "vm_params", "")),
            ("job", getattr(mach, "job", "")),
            ("job_pid", getattr(mach, "job_pid", "")),
            ("startcmd", getattr(mach, "startcmd", "")),
            ("stopcmd", getattr(mach, "stopcmd", "")),
            ("powercmd", getattr(mach, "powercmd", "")),
            ("start_timeout", str(getattr(mach, "start_timeout", "240"))),
        ]

        for tag, val in fields:
            child = target_elem.find(tag)
            if child is None:
                child = ET.SubElement(target_elem, tag)
            child.text = sanitize_text(str(val) if val is not None else "")

        return cls.save()

    @classmethod
    def add_machine(cls, mach: Any) -> bool:
        cls.load()
        if cls.settings is None:
            cls.settings = ET.Element("settings")

        m_elem = ET.SubElement(cls.settings, "machine")
        fields = [
            ("name", mach.name),
            ("mac", mach.mac),
            ("ip", getattr(mach, "ip", "")),
            ("is_started", str(getattr(mach, "_is_started", "0"))),
            ("only_vm", str(getattr(mach, "only_vm", "0"))),
            ("arch", getattr(mach, "arch", "")),
            ("os", getattr(mach, "os", "")),
            ("kernel", getattr(mach, "kernel", "")),
            ("reservation", getattr(mach, "reservation", "")),
            ("resources", getattr(mach, "resources", "")),
            ("boot_params", getattr(mach, "boot_params", "")),
            ("vm_params", getattr(mach, "vm_params", "")),
            ("job", getattr(mach, "job", "")),
            ("job_pid", getattr(mach, "job_pid", "")),
            ("startcmd", getattr(mach, "startcmd", "")),
            ("stopcmd", getattr(mach, "stopcmd", "")),
            ("powercmd", getattr(mach, "powercmd", "")),
            ("start_timeout", str(getattr(mach, "start_timeout", "240"))),
        ]

        for tag, val in fields:
            child = ET.SubElement(m_elem, tag)
            child.text = sanitize_text(str(val) if val is not None else "")

        return cls.save()

    @classmethod
    def delete_machine(cls, mach: Any) -> bool:
        cls.load()
        if cls.settings is None:
            return False

        to_remove = []
        for elem in cls.settings.findall("machine"):
            mac_elem = elem.find("mac")
            name_elem = elem.find("name")
            mac_val = mac_elem.text if mac_elem is not None and mac_elem.text else ""
            name_val = name_elem.text if name_elem is not None and name_elem.text else ""

            if (mach.mac and mac_val.lower() == mach.mac.lower()) or (mach.name and name_val == mach.name):
                to_remove.append(elem)

        for elem in to_remove:
            cls.settings.remove(elem)

        return cls.save()


# Backward compatibility alias
Settings = MachineRepository


def LOCK() -> None:
    MachineRepository.lock()


def UNLOCK() -> None:
    MachineRepository.unlock()


def IS_LOCKED() -> bool:
    return MachineRepository.is_lock_held()


def SLEEP_ON_LOCK(seconds: int) -> None:
    UNLOCK()
    time.sleep(seconds)
    LOCK()


def select_machine(arg: Any = False) -> Any:
    from main import select_machine as _select_machine
    return _select_machine(arg)


def __getattr__(name: str) -> Any:
    if name == "Machine":
        from machine import Machine
        return Machine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")