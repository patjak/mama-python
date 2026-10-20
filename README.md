# MAMA (Machine Administration & Management Architecture)

MAMA is an automated provisioning, network booting (iPXE/NFS), and machine management framework for bare-metal and virtual (QEMU/KVM) test machines.

---

## Directory Layout: What Goes Where

MAMA follows the Linux Filesystem Hierarchy Standard (FHS) to separate unprivileged user-accessible metadata, logs, and runtime state from target root filesystems.

```text
/
├── var/
│   ├── lib/mama/                  # Persistent State & Database (root:mama, 0775)
│   │   ├── mama.db                # SQLite machine & reservation inventory
│   │   ├── mama.db-wal            # SQLite Write-Ahead Log
│   │   └── mama.db-shm            # SQLite shared-memory index
│   │
│   └── log/mama/                  # Operational & Console Logs (root:mama, 0775)
│       ├── mama-log               # Main operational log
│       └── <machine>-vmlog        # Serial console logs for virtual machines
│
├── run/mama/                      # Volatile Runtime State (tmpfs, root:mama, 0775)
│   ├── <machine>.pid              # QEMU VM PID tracking
│   └── locks/                     # Advisory concurrency locks (fcntl flock)
│       ├── <machine>.lock         # Per-machine locks
│       └── os/                    # Per-OS build template locks
│
└── usr/share/mama/                # Payload, Templates & Boot Assets (root:root, 0755)
    ├── machines/                  # Target machine deployment root filesystems
    │   └── <name>/<arch>/<os>/    # Extracted NFS rootfs (strict guest permissions!)
    ├── os/                        # Master OS rootfs templates built by Kiwi-ng
    ├── os-descriptions/           # Kiwi XML appliance descriptions & config files
    ├── jobs/                      # Multi-stage automated job scripts
    └── tftp/                      # dnsmasq TFTP root (autoexec.ipxe, iPXE binaries)
```

---

## Permissions & User Access

- **Target Root Filesystems (`/usr/share/mama/machines/` and `/usr/share/mama/os/`)**:
  - Kept strictly owned by `root:root` with standard guest permissions.
  - SUID binaries (`/usr/bin/sudo`), `/etc/shadow`, and guest daemon UIDs/GIDs are completely isolated and never altered by normal users.
- **Metadata & Reservations (`/var/lib/mama/` and `/run/mama/locks/`)**:
  - Owned by `root:mama` with mode `2775` (setgid).
  - Members of the `mama` system group can run everyday management commands (`mama reserve`, `mama release`, `mama list`, `mama log`, `mama wait`) without requiring `sudo`.
- **Root Operations**:
  - Root privileges are required only for building OS images (`mama build-os`), starting network services (`mama serve`), or managing system-level network configurations.
- **Sudo Awareness**:
  - When commands are run with `sudo`, MAMA inspects `$SUDO_USER` to ensure reservations are attributed to the real human user rather than `root`.

---

## Installation & Setup

### Initial Installation

Run the installation script as root (or via sudo):

```bash
sudo ./install.sh
```

The script will automatically:
1. Create the `mama` system group and add the invoking user (`$SUDO_USER`).
2. Create `/var/lib/mama`, `/var/log/mama`, and `/usr/share/mama` directories with proper permissions.
3. Install `/etc/tmpfiles.d/mama.conf` so `/run/mama/locks` is recreated on system reboot.
4. If an existing `mama.db` or logs exist in `/usr/share/mama/`, migrate them to `/var/lib/mama/`.
5. Install and reload `mama-boot.service` for systemd.
6. Symlink `mama` into `/usr/local/bin/mama` and `/usr/bin/mama`.

> **Note**: If your user was just added to the `mama` group, log out and log back in (or run `newgrp mama`) for group membership to take effect.

### Moving the Repository

If you relocate or clone the repository to a new path, simply rerun:

```bash
cd /new/path/to/mama-python
sudo ./install.sh
```

The script will update the system symlinks to point to the new location. All database records and machine root filesystems remain untouched.

### Uninstallation

To remove systemd services, tmpfiles configs, and CLI symlinks:

```bash
sudo ./uninstall.sh
```

*(Data directories `/var/lib/mama`, `/var/log/mama`, and `/usr/share/mama` are preserved).*

---

## Configuration & Environment Overrides

Paths and defaults can be customized via environment variables:

| Variable | Default Path | Description |
| :--- | :--- | :--- |
| `MAMA_PATH` | `/usr/share/mama` | Base directory for rootfs payloads and templates |
| `MAMA_STATE_DIR` | `/var/lib/mama` | Persistent state directory |
| `MAMA_DB_FILE` | `/var/lib/mama/mama.db` | SQLite database file |
| `MAMA_RUN_DIR` | `/run/mama` | Runtime PID and socket directory |
| `MAMA_LOCK_DIR` | `/run/mama/locks` | Advisory filesystem locks directory |
| `MAMA_LOG_DIR` | `/var/log/mama` | Operational and console log directory |
| `MAMA_HOST` | `192.168.2.1` | Host IP address served to DHCP/NFS clients |

---

## Quick Command Reference

```bash
# List all registered machines and their current state
mama list

# Reserve a machine (records your username)
mama reserve <machine-name>

# Release a reserved machine
mama release <machine-name>

# Connect to a machine via SSH
mama connect <machine-name>

# View or follow logs
mama log <machine-name> -f

# Boot or control a virtual machine
mama start-vm <machine-name>
mama stop <machine-name>

# Start iPXE boot and dynamic HTTP service
mama serve
```
