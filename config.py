import pathlib

# Core Paths
MAMA_PATH: str = "/usr/share/mama"
DB_FILE: str = f"{MAMA_PATH}/mama.db"

# Network Configuration
MAMA_HOST: str = "192.168.2.1"
TFTP_PORT: int = 69
TFTP_DIR: str = f"{MAMA_PATH}/tftp"

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