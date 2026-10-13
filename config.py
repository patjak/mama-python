import pathlib

# Core Paths
MAMA_PATH: str = "/usr/share/mama"
DB_FILE: str = f"{MAMA_PATH}/mama.db"

# Network Configuration
MAMA_HOST: str = "192.168.2.1"
TFTP_PORT: int = 69
TFTP_DIR: str = f"{MAMA_PATH}/tftp"

# DHCP & dnsmasq Configuration (openSUSE)
DHCP_ENABLED: bool = True
DHCP_INTERFACE: str = "br0"
DHCP_RANGE_START: str = "192.168.2.100"
DHCP_RANGE_END: str = "192.168.2.200"
DHCP_NETMASK: str = "255.255.255.0"
DHCP_LEASE_TIME: str = "12h"
DNSMASQ_CONF_PATH: str = "/etc/dnsmasq.d/mama.conf"
DNSMASQ_LEASES_FILE: str = "/var/lib/misc/dnsmasq.leases"

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
