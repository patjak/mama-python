from __future__ import annotations

import os
import pathlib

# Payload & Static Template Paths (Rootfs, descriptions, jobs, TFTP)
MAMA_PATH: str = os.environ.get("MAMA_PATH", "/usr/share/mama")
MACHINES_PATH: str = os.environ.get("MAMA_MACHINES_PATH", f"{MAMA_PATH}/machines")
OS_PATH: str = os.environ.get("MAMA_OS_PATH", f"{MAMA_PATH}/os")
OS_DESC_PATH: str = os.environ.get("MAMA_OS_DESC_PATH", f"{MAMA_PATH}/os-descriptions")
JOBS_PATH: str = os.environ.get("MAMA_JOBS_PATH", f"{MAMA_PATH}/jobs")
TFTP_DIR: str = os.environ.get("MAMA_TFTP_DIR", f"{MAMA_PATH}/tftp")

# Persistent State Directory (Database)
STATE_DIR: str = os.environ.get("MAMA_STATE_DIR", "/var/lib/mama")


def _resolve_db_file() -> str:
    if "MAMA_DB_FILE" in os.environ:
        return os.environ["MAMA_DB_FILE"]
    var_db = pathlib.Path(STATE_DIR) / "mama.db"
    share_db = pathlib.Path(MAMA_PATH) / "mama.db"
    if var_db.exists():
        return str(var_db)
    if share_db.exists():
        return str(share_db)
    return str(var_db)


DB_FILE: str = _resolve_db_file()

# Runtime State & Advisory Locks (tmpfs / run)
RUN_DIR: str = os.environ.get("MAMA_RUN_DIR", "/run/mama")
LOCK_DIR: str = os.environ.get("MAMA_LOCK_DIR", f"{RUN_DIR}/locks")

# Operational Logs Directory
def _resolve_log_dir() -> str:
    if "MAMA_LOG_DIR" in os.environ:
        return os.environ["MAMA_LOG_DIR"]
    var_log = pathlib.Path("/var/log/mama")
    share_log = pathlib.Path(MAMA_PATH) / "log"
    if var_log.exists():
        return str(var_log)
    if share_log.exists() and not var_log.exists():
        return str(share_log)
    return str(var_log)


LOG_DIR: str = _resolve_log_dir()

# Network Configuration
MAMA_HOST: str = os.environ.get("MAMA_HOST", "192.168.2.1")

# Package Cache Proxy (apt-cacher-ng)
PACKAGE_PROXY_ENABLED: bool = True
PACKAGE_PROXY_HOST: str = MAMA_HOST
PACKAGE_PROXY_PORT: int = 3142

# DHCP & dnsmasq Configuration (openSUSE)
DHCP_ENABLED: bool = True
DHCP_INTERFACE: str = "br0"
DHCP_RANGE_START: str = "192.168.2.100"
DHCP_RANGE_END: str = "192.168.2.200"
DHCP_NETMASK: str = "255.255.255.0"
DHCP_LEASE_TIME: str = "12h"
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

# Machine Management Delays and Timeouts (in seconds, unless noted)
START_TIMEOUT_DEFAULT: int = 4 * 60  # Default timeout to wait for machine boot
STOP_TIMEOUT: int = 30  # Timeout to wait for machine power-off
SSH_CMD_TIMEOUT: int = 1 * 60  # SSH command execution timeout (in minutes)
POWER_CYCLE_DELAY: int = 30  # Wait time after power down before powering on
RESOURCE_WAIT_TIMEOUT: int = 20 * 60 * 60  # Max wait time for resource acquisition
