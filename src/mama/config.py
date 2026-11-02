from __future__ import annotations

import configparser
import os
import pathlib
from typing import Any, Dict, Optional, Union

# Core Paths
MAMA_PATH: str = "/usr/share/mama"
DB_FILE: str = f"{MAMA_PATH}/mama.db"
CONF_FILE: str = "/etc/mama/mama.conf"

# Network Configuration
MAMA_HOST: str = "192.168.2.1"
TFTP_DIR: str = f"{MAMA_PATH}/tftp"  # Root directory served by dnsmasq TFTP

# Package Cache Proxy (apt-cacher-ng)
PACKAGE_PROXY_ENABLED: bool = False
PACKAGE_PROXY_HOST: str = MAMA_HOST
PACKAGE_PROXY_PORT: int = 3142

# DHCP & dnsmasq Configuration (openSUSE)
DHCP_ENABLED: bool = True
DHCP_INTERFACE: str = "br0"
DHCP_RANGE_START: str = "192.168.2.100"
DHCP_RANGE_END: str = "192.168.2.200"
DHCP_NETMASK: str = "255.255.255.0"
DHCP_LEASE_TIME: str = "12h"
DHCP_MTU: Optional[int] = 1450  # Interface MTU (DHCP Option 26, e.g. 1500 or 9000)
DNSMASQ_CONF_PATH: str = "/etc/dnsmasq.d/mama.conf"
DNSMASQ_LEASES_FILE: str = "/var/lib/misc/dnsmasq.leases"

# NFS Server Configuration (openSUSE)
NFS_ENABLED: bool = True
NFS_SERVICE_NAME: str = "nfs-server"
NFS_EXPORTS_PATH: str = "/etc/exports.d/mama.exports"
NFS_OPTIONS: str = "rw,async,no_root_squash,no_subtree_check,fsid=0"

# Debug Flags
DEBUG: bool = False
DEBUG_LOCK: bool = False
DEBUG_PID: bool = False

# Virtual Machine Defaults
VM_DEFAULT_RAM_MB: Union[int, str] = "auto"
VM_HOST_RAM_RESERVE_MB: int = 4096
VM_DEFAULT_CPUS: Optional[int] = None

# Machine Management Delays and Timeouts (in seconds, unless noted)
START_TIMEOUT_DEFAULT: int = 4 * 60  # Default timeout to wait for machine boot
STOP_TIMEOUT: int = 30  # Timeout to wait for machine power-off
SSH_CMD_TIMEOUT: int = 1 * 60  # SSH command execution timeout (in minutes)
POWER_CYCLE_DELAY: int = 30  # Wait time after power down before powering on
RESOURCE_WAIT_TIMEOUT: int = 20 * 60 * 60  # Max wait time for resource acquisition


def _parse_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def load_config(conf_path: Optional[str] = None) -> None:
    """Load configuration from /etc/mama/mama.conf (or custom path) and environment variables."""
    global MAMA_PATH, DB_FILE, CONF_FILE, MAMA_HOST, TFTP_DIR
    global PACKAGE_PROXY_ENABLED, PACKAGE_PROXY_HOST, PACKAGE_PROXY_PORT
    global DHCP_ENABLED, DHCP_INTERFACE, DHCP_RANGE_START, DHCP_RANGE_END
    global DHCP_NETMASK, DHCP_LEASE_TIME, DHCP_MTU, DNSMASQ_CONF_PATH, DNSMASQ_LEASES_FILE
    global NFS_ENABLED, NFS_SERVICE_NAME, NFS_EXPORTS_PATH, NFS_OPTIONS
    global DEBUG, DEBUG_LOCK, DEBUG_PID, VM_DEFAULT_RAM_MB, VM_HOST_RAM_RESERVE_MB, VM_DEFAULT_CPUS
    global START_TIMEOUT_DEFAULT, STOP_TIMEOUT, SSH_CMD_TIMEOUT, POWER_CYCLE_DELAY, RESOURCE_WAIT_TIMEOUT

    target_conf = conf_path or os.environ.get("MAMA_CONF") or CONF_FILE
    p = pathlib.Path(target_conf)

    data: Dict[str, str] = {}
    if p.is_file():
        cp = configparser.ConfigParser()
        try:
            content = p.read_text(encoding="utf-8")
            # If no section header is present, treat as [mama] section
            if not any(line.strip().startswith("[") for line in content.splitlines() if line.strip()):
                content = f"[mama]\n{content}"
            cp.read_string(content)
            for section in cp.sections():
                for k, v in cp.items(section):
                    data[k.upper()] = v
        except Exception:
            pass

    def get_val(key: str, default: Any, val_type: type = str) -> Any:
        raw = os.environ.get(key)
        if raw is None:
            raw = data.get(key)
        if raw is None:
            return default

        if val_type == bool:
            return _parse_bool(raw)
        elif val_type == int:
            try:
                return int(raw)
            except (ValueError, TypeError):
                return default
        return str(raw).strip()

    # Core Paths
    MAMA_PATH = get_val("MAMA_PATH", MAMA_PATH)
    DB_FILE = get_val("DB_FILE", f"{MAMA_PATH}/mama.db")
    MAMA_HOST = get_val("MAMA_HOST", MAMA_HOST)
    TFTP_DIR = get_val("TFTP_DIR", f"{MAMA_PATH}/tftp")

    # Package Proxy
    PACKAGE_PROXY_ENABLED = get_val("PACKAGE_PROXY_ENABLED", PACKAGE_PROXY_ENABLED, bool)
    PACKAGE_PROXY_HOST = get_val("PACKAGE_PROXY_HOST", MAMA_HOST)
    PACKAGE_PROXY_PORT = get_val("PACKAGE_PROXY_PORT", PACKAGE_PROXY_PORT, int)

    # DHCP
    DHCP_ENABLED = get_val("DHCP_ENABLED", DHCP_ENABLED, bool)
    DHCP_INTERFACE = get_val("DHCP_INTERFACE", DHCP_INTERFACE)
    DHCP_RANGE_START = get_val("DHCP_RANGE_START", DHCP_RANGE_START)
    DHCP_RANGE_END = get_val("DHCP_RANGE_END", DHCP_RANGE_END)
    DHCP_NETMASK = get_val("DHCP_NETMASK", DHCP_NETMASK)
    DHCP_LEASE_TIME = get_val("DHCP_LEASE_TIME", DHCP_LEASE_TIME)
    DNSMASQ_CONF_PATH = get_val("DNSMASQ_CONF_PATH", DNSMASQ_CONF_PATH)
    DNSMASQ_LEASES_FILE = get_val("DNSMASQ_LEASES_FILE", DNSMASQ_LEASES_FILE)

    raw_mtu = os.environ.get("DHCP_MTU") or os.environ.get("MTU") or data.get("DHCP_MTU") or data.get("MTU")
    if raw_mtu:
        try:
            DHCP_MTU = int(raw_mtu)
        except (ValueError, TypeError):
            DHCP_MTU = None
    else:
        DHCP_MTU = None

    # NFS
    NFS_ENABLED = get_val("NFS_ENABLED", NFS_ENABLED, bool)
    NFS_SERVICE_NAME = get_val("NFS_SERVICE_NAME", NFS_SERVICE_NAME)
    NFS_EXPORTS_PATH = get_val("NFS_EXPORTS_PATH", NFS_EXPORTS_PATH)
    NFS_OPTIONS = get_val("NFS_OPTIONS", NFS_OPTIONS)

    # Debug Flags
    DEBUG = get_val("DEBUG", DEBUG, bool)
    DEBUG_LOCK = get_val("DEBUG_LOCK", DEBUG_LOCK, bool)
    DEBUG_PID = get_val("DEBUG_PID", DEBUG_PID, bool)

    # VM Defaults
    raw_ram = os.environ.get("VM_DEFAULT_RAM_MB")
    if raw_ram is None:
        raw_ram = data.get("VM_DEFAULT_RAM_MB")
    if raw_ram is not None:
        raw_ram_str = str(raw_ram).strip().lower()
        if raw_ram_str in ("auto", "dynamic", "max"):
            VM_DEFAULT_RAM_MB = "auto"
        else:
            try:
                VM_DEFAULT_RAM_MB = int(raw_ram_str)
            except ValueError:
                VM_DEFAULT_RAM_MB = "auto"

    VM_HOST_RAM_RESERVE_MB = get_val("VM_HOST_RAM_RESERVE_MB", VM_HOST_RAM_RESERVE_MB, int)
    raw_cpus = os.environ.get("VM_DEFAULT_CPUS") or data.get("VM_DEFAULT_CPUS")
    if raw_cpus:
        try:
            VM_DEFAULT_CPUS = int(raw_cpus)
        except (ValueError, TypeError):
            pass

    # Timeouts
    START_TIMEOUT_DEFAULT = get_val("START_TIMEOUT_DEFAULT", START_TIMEOUT_DEFAULT, int)
    STOP_TIMEOUT = get_val("STOP_TIMEOUT", STOP_TIMEOUT, int)
    SSH_CMD_TIMEOUT = get_val("SSH_CMD_TIMEOUT", SSH_CMD_TIMEOUT, int)
    POWER_CYCLE_DELAY = get_val("POWER_CYCLE_DELAY", POWER_CYCLE_DELAY, int)
    RESOURCE_WAIT_TIMEOUT = get_val("RESOURCE_WAIT_TIMEOUT", RESOURCE_WAIT_TIMEOUT, int)


# Load active configuration on module import
load_config()
