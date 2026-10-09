from __future__ import annotations

from typing import Any, Dict, List, Optional


class CtlDev:
    devs: List[CtlDev] = []

    def __init__(self, name: str, slots: int, device_path_or_ip: str) -> None:
        self.name: str = name
        self.slots: int = slots
        self.device_path_or_ip: str = device_path_or_ip
        CtlDev.devs.append(self)

    @classmethod
    def get_by_name(cls, name: str) -> Optional[CtlDev]:
        for dev in cls.devs:
            if dev.name == name:
                return dev
        return None

    def set_power(self, slot: Any, val: Any) -> None:
        pass

    def push(self, slot: Any, val: Any) -> None:
        pass

    def get_sensors(self, slot: Any) -> Optional[Dict[str, Any]]:
        return None

    def get_sensors_all_slots(self) -> Optional[List[Dict[str, Any]]]:
        return None