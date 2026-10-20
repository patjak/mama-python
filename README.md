# MAMA (Machine Administration & Management Architecture)

MAMA is an automated provisioning, network booting (iPXE/NFS), and machine management framework for bare-metal and virtual (QEMU/KVM) test machines.

---

## Architecture & Directory Layout

All operational assets and target root filesystems reside under `/usr/share/mama` (which is exported over NFS to target and worker machines):

```text
/usr/share/mama/
├── mama.db                 # SQLite inventory & reservations (root:mama, 0664)
├── mama.db-wal / -shm      # SQLite WAL and shared-memory index
├── log/                    # Operational & VM console logs (root:mama, 2775)
│   ├── mama-log            # Central operational log
│   └── <machine>-vmlog     # Serial console logs for VMs
├── machines/               # Target machine root filesystems (root:root, 0755)
│   └── <name>/<arch>/<os>/ # Extracted Linux NFS rootfs (strict guest permissions!)
├── os/                     # Master OS rootfs templates built by Kiwi-ng (root:root, 0755)
├── os-descriptions/        # Kiwi XML appliance descriptions & config files
├── jobs/                   # Automated job definitions (e.g. os-builder, igt, drm-tip)
└── tftp/                   # dnsmasq TFTP root (autoexec.ipxe, iPXE binaries)
```

---

## Permissions & Multi-User Access

MAMA uses targeted **non-recursive** group permissions to allow normal developers to manage machines without compromising guest OS root filesystems:

1. **Non-Root Machine Reservations**:
   - The directory `/usr/share/mama` and `/usr/share/mama/log` are setgid `root:mama` (`mode 2775`).
   - `mama.db*` files are owned by `root:mama` (`mode 0664`).
   - Any member of the `mama` system group can run everyday management commands (`mama reserve`, `mama release`, `mama list`, `mama log`, `mama wait`) without `sudo`.

2. **Guest Rootfs Integrity**:
   - The `/usr/share/mama/machines/` and `/usr/share/mama/os/` trees remain strictly `root:root` with mode `0755`.
   - Guest security files (`/etc/shadow`, `/etc/sudoers`), guest daemon UIDs/GIDs, and SUID binaries are never modified or compromised.

3. **Remote Worker & Builder Support**:
   - Remote machines (such as `os-builder` or test workers) mount `/usr/share/mama` over NFS.
   - Because `mama.db`, scripts, and OS descriptions live on the shared NFS export, remote workers can access the shared environment seamlessly.

4. **Sudo Awareness**:
   - When running commands via `sudo`, MAMA inspects `$SUDO_USER` to attribute machine reservations to the real human user rather than `root`.

---

## Installation & Setup

### Initial Installation

Run the install script with root privileges:

```bash
sudo ./install.sh
```

The script will:
1. Create the `mama` system group and automatically add your user (`$SUDO_USER`).
2. Create standard directories in `/usr/share/mama`.
3. Apply targeted, non-recursive permissions (`group mama`, `mode 2775/0664`).
4. Install `mama-boot.service` into systemd.
5. Symlink the CLI runner to `/usr/local/bin/mama` and `/usr/bin/mama`.

> **Note**: If your user was just added to the `mama` group, log out and back in (or run `newgrp mama` in your active shell) for group membership to take effect.

### Moving the Repository

If you move the repository to a new path, simply rerun:

```bash
cd /new/path/to/mama-python
sudo ./install.sh
```

The symlinks in `/usr/local/bin/mama` and `/usr/bin/mama` will be updated to point to the new location.

### Development Workflow

You do **not** need to rerun `install.sh` when modifying Python code. The CLI symlinks directly to `src/` in your repository, so any changes made to files in `src/mama/` take effect immediately.

### Uninstallation

To remove systemd services and CLI symlinks while keeping data intact:

```bash
sudo ./uninstall.sh
```

---

## Quick Command Reference

```bash
# List all machines and current state
mama list

# Reserve a machine (records your username)
mama reserve <machine-name>

# Release a reserved machine
mama release <machine-name>

# Wait for a machine to become idle
mama wait <machine-name>

# Connect to a machine via SSH
mama connect <machine-name>

# View or follow logs
mama log <machine-name> -f

# Start or stop a virtual machine
mama start-vm <machine-name>
mama stop <machine-name>

# Start iPXE boot server
mama serve
```
