import pathlib

# Core Paths
MAMA_PATH: str = "/usr/share/mama"
SETTINGS_FILE: str = f"{MAMA_PATH}/mama.xml"

# Network Configuration
MAMA_HOST: str = "192.168.2.1"

# Debug Flags
DEBUG: bool = False
DEBUG_LOCK: bool = False
DEBUG_PID: bool = False

# Machine Management Delays and Timeouts (in seconds, unless noted)
START_TIMEOUT_DEFAULT: int = 4 * 60  # Default timeout to wait for machine boot
STOP_TIMEOUT: int = 30  # Timeout to wait for machine power-off
SSH_CMD_TIMEOUT: int = 1 * 60  # SSH command execution timeout (in minutes)
RLY_FORCE_OFF_DELAY: int = 8  # Delay for relay force off
POWER_CYCLE_DELAY: int = 30  # Wait time after power down before powering on
RESOURCE_WAIT_TIMEOUT: int = 20 * 60 * 60  # Max wait time for resource acquisition

# Hardware Control Devices Configuration
# Import and instantiate custom CtlDev subclasses (e.g., MpowerCtlDev, UsbrlyCtlDev) here.
# Example:
# from ctldev import MpowerCtlDev, UsbrlyCtlDev

# Import your mPower control device class
from mpower import MpowerCtlDev

MpowerCtlDev("mpower1", 6, "192.168.2.2")
MpowerCtlDev("mpower2", 6, "192.168.2.3")
# UsbrlyCtlDev("usbrly1", 8, "/dev/ttyACM0")

