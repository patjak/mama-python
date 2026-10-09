from __future__ import annotations

import fcntl
import html
import os
import pathlib
import subprocess
import time
import xml.dom.minidom
import xml.etree.ElementTree as ET
from typing import Any, List, Optional, Union

import config
from logger import error, fatal
from util import Util


def LOCK() -> None:
    Settings.lock()


def UNLOCK() -> None:
    Settings.unlock()


def IS_LOCKED() -> bool:
    return Settings.is_lock_held()


def SLEEP_ON_LOCK(seconds: int) -> None:
    UNLOCK()
    if IS_LOCKED():
        fatal("Sleeping while lock is held")
    time.sleep(seconds)
    LOCK()


class SettingsElement:
    def __init__(self, element: ET.Element) -> None:
        self._element = element

    @property
    def machine(self) -> List[ET.Element]:
        return self._element.findall("machine")

    @property
    def resource(self) -> List[ET.Element]:
        return self._element.findall("resource")

    def addChild(self, tag: str, value: Optional[str] = None) -> SettingsElement:
        child = ET.SubElement(self._element, tag)
        if value is not None:
            child.text = str(value)
        return SettingsElement(child)

    def removeChild(self, child: Union[ET.Element, SettingsElement]) -> None:
        elem = child._element if isinstance(child, SettingsElement) else child
        self._element.remove(elem)

    def asXML(self) -> str:
        return ET.tostring(self._element, encoding="unicode")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._element, name)


class Settings:
    _lock: int = 0
    _stream: Optional[Any] = None
    settings: Optional[SettingsElement] = None

    @classmethod
    def lock(cls) -> bool:
        if getattr(config, "DEBUG_LOCK", False):
            pid_color = Util.string_to_rand_color(str(os.getpid()))
            msg = f"{pid_color}: Aquiring lock: {cls._lock + 1}\n"
            lock_debug = pathlib.Path(config.MAMA_PATH) / "mama-lock-debug"
            with open(lock_debug, "a") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                f.write(msg)

        if cls._lock == 0:
            if cls._stream is not None:
                fatal("Tried to aquire an already aquired lock")

            try:
                cls._stream = open(config.SETTINGS_FILE, "r+")
                fcntl.flock(cls._stream.fileno(), fcntl.LOCK_EX)
            except Exception as e:
                fatal(f"Failed to open or lock settings file ({config.SETTINGS_FILE}): {e}")

        cls._lock += 1
        return True

    @classmethod
    def unlock(cls) -> None:
        if getattr(config, "DEBUG_LOCK", False):
            msg = f"PID {os.getpid()}: Releasing lock: {cls._lock}\n"
            lock_debug = pathlib.Path(config.MAMA_PATH) / "mama-lock-debug"
            with open(lock_debug, "a") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                f.write(msg)

        if cls._lock == 0:
            fatal("mama.xml is not locked")

        cls._lock -= 1

        if cls._lock == 0 and cls._stream is not None:
            try:
                fcntl.flock(cls._stream.fileno(), fcntl.LOCK_UN)
                cls._stream.close()
            finally:
                cls._stream = None

    @classmethod
    def is_lock_held(cls) -> bool:
        return cls._lock != 0

    @classmethod
    def get_contents(cls) -> str:
        cls.lock()

        if cls._stream is None:
            cls.unlock()
            fatal("Failed to read mama.xml: stream is not open")

        cls._stream.seek(0, os.SEEK_END)
        filesize = cls._stream.tell()
        cls._stream.seek(0)

        content = cls._stream.read()
        cls.unlock()

        if len(content) != filesize:
            fatal(f"Size mismatch: {len(content)} != {filesize}")

        if "</settings>" not in content:
            error("Invalid mama.xml")
            print(content)
            raise SystemExit(1)

        return content

    @classmethod
    def put_contents(cls, str_data: str) -> str:
        if not str_data:
            fatal("FATAL: Trying to write an empty mama.xml")

        cls.lock()
        if cls._stream is None:
            cls.unlock()
            fatal("Failed to write mama.xml: stream is not open")

        try:
            cls._stream.seek(0)
            cls._stream.truncate(0)
            cls._stream.write(str_data)
            cls._stream.flush()
        except Exception as e:
            cls.unlock()
            fatal(f"Failed to write mama.xml: {e}")

        cls.unlock()
        return str_data

    @classmethod
    def load(cls) -> Optional[SettingsElement]:
        settings_path = pathlib.Path(config.SETTINGS_FILE)

        if not settings_path.exists():
            default_xml = pathlib.Path(config.MAMA_PATH) / "mama.xml-default"
            res = subprocess.run(f"cp {default_xml} {settings_path}", shell=True)
            if res.returncode != 0:
                fatal("Failed to install the mama.xml file")

            res = subprocess.run(f"chgrp mama {settings_path}", shell=True)
            if res.returncode != 0:
                fatal("Failed to set permission on mama.xml file")

            res = subprocess.run(f"chmod 660 {settings_path}", shell=True)
            if res.returncode != 0:
                fatal("Failed to set permission on mama.xml file")

        if not settings_path.exists():
            fatal("No mama.xml file found")

        content = cls.get_contents()
        root_element = ET.fromstring(content)
        cls.settings = SettingsElement(root_element)

        return cls.settings

    @staticmethod
    def make_xml_great_again(xml_str: str) -> str:
        dom = xml.dom.minidom.parseString(xml_str)
        pretty = dom.toprettyxml(indent="  ")
        lines = [line for line in pretty.splitlines() if line.strip()]
        return "\n".join(lines) + "\n"

    @classmethod
    def save(cls) -> None:
        if cls.settings is None:
            fatal("No settings loaded to save")

        raw_xml = cls.settings.asXML()
        formatted_xml = cls.make_xml_great_again(raw_xml)

        if cls.put_contents(formatted_xml) is False:
            fatal(f"Unable to save settings file: {config.SETTINGS_FILE}")

    @classmethod
    def _get_machine_element(cls, mach: Any) -> Optional[ET.Element]:
        if cls.settings is None:
            return None
        mach_name = getattr(mach, "name", str(mach))
        for m in cls.settings.machine:
            name_node = m.find("name")
            if name_node is not None and name_node.text == mach_name:
                return m
        return None

    @classmethod
    def delete_machine(cls, mach: Any) -> None:
        cls.lock()
        cls.load()

        elem = cls._get_machine_element(mach)
        if elem is not None and cls.settings is not None:
            cls.settings.removeChild(elem)

        cls.save()
        cls.unlock()

    @classmethod
    def update_machine(cls, mach: Any) -> None:
        cls.lock()
        cls.delete_machine(mach)

        attrs = [
            "name", "mac", "ip", "is_started", "_is_started", "only_vm", "arch", "os",
            "kernel", "pwr_dev", "pwr_slot", "rly_dev", "rly_slot",
            "reservation", "resources", "boot_params", "vm_params",
            "job", "job_pid", "startcmd", "stopcmd", "start_timeout"
        ]
        for attr in attrs:
            val = getattr(mach, attr, None)
            if val is not None and not callable(val):
                setattr(mach, attr, html.escape(str(val), quote=True))

        cls.add_machine(mach)

        for attr in attrs:
            val = getattr(mach, attr, None)
            if val is not None and not callable(val):
                setattr(mach, attr, html.unescape(str(val)))

        cls.unlock()

    @classmethod
    def add_machine(cls, mach: Any) -> None:
        cls.lock()
        cls.load()

        if cls.settings is not None:
            m = cls.settings.addChild("machine")
            m.addChild("name", getattr(mach, "name", ""))
            m.addChild("mac", getattr(mach, "mac", ""))
            m.addChild("ip", getattr(mach, "ip", ""))

            is_started_val = getattr(mach, "_is_started", None)
            if is_started_val is None or callable(is_started_val):
                if hasattr(mach, "is_started") and callable(mach.is_started):
                    is_started_val = "1" if mach.is_started() else "0"
                else:
                    is_started_val = "0"
            m.addChild("is_started", str(is_started_val))

            m.addChild("only_vm", getattr(mach, "only_vm", ""))
            m.addChild("arch", getattr(mach, "arch", ""))
            m.addChild("os", getattr(mach, "os", ""))
            m.addChild("kernel", getattr(mach, "kernel", ""))
            m.addChild("pwr_dev", getattr(mach, "pwr_dev", ""))
            m.addChild("pwr_slot", getattr(mach, "pwr_slot", ""))
            m.addChild("rly_dev", getattr(mach, "rly_dev", ""))
            m.addChild("rly_slot", getattr(mach, "rly_slot", ""))
            m.addChild("reservation", getattr(mach, "reservation", ""))
            m.addChild("resources", getattr(mach, "resources", ""))
            m.addChild("boot_params", getattr(mach, "boot_params", ""))
            m.addChild("vm_params", getattr(mach, "vm_params", ""))
            m.addChild("job", getattr(mach, "job", ""))
            m.addChild("job_pid", getattr(mach, "job_pid", ""))
            m.addChild("startcmd", getattr(mach, "startcmd", ""))
            m.addChild("stopcmd", getattr(mach, "stopcmd", ""))
            m.addChild("start_timeout", getattr(mach, "start_timeout", ""))

        cls.save()
        cls.unlock()