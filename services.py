from __future__ import annotations

from typing import Any, Union

from ctldev import CtlDev
from settings import IS_LOCKED, LOCK, SLEEP_ON_LOCK, UNLOCK, Settings
from util import Util


def select_machine(arg: Union[str, bool, None] = False) -> Any:
    """Select or look up a machine by name or MAC address."""
    import logger
    from machine import Machine

    logger.Log.set_file(False)

    machs = Machine.get_all()
    m_found = None

    if arg:
        arg_str = str(arg)
        for mach in machs:
            if arg_str == mach.name or arg_str == mach.mac:
                m_found = mach
                break

    if not m_found and arg:
        logger.error(f"Machine with name/mac {arg} not found")
        return False

    if not arg:
        from main import cmd_list
        cmd_list()
        selected = Util.ask_from_array(machs, "Machine: ")
        if not selected:
            return False
        m_found = selected

    mach = Machine()
    if m_found:
        mach.fill_from_xmlobj(m_found)
        logger.Log.set_file(mach.name)

    return mach


def __getattr__(name: str) -> Any:
    """Lazy-load Machine and Os to prevent circular import loops."""
    if name == "Machine":
        from machine import Machine
        return Machine
    if name == "Os":
        from os_mgr import Os
        return Os
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")