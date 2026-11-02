#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# MAMA (Machine Administration & Management Architecture) Installer
# Configures system groups, non-recursive permissions on /usr/share/mama,
# QEMU bridge helper, NFS exports, iPXE binaries, systemd service, and CLI links.
# ==============================================================================

if [ "$(id -u)" -ne 0 ]; then
    echo "Error: install.sh must be run as root (e.g. sudo ./install.sh)" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Installing MAMA ==="

# 1. System Group Setup
if ! getent group mama >/dev/null 2>&1; then
    echo "Creating system group 'mama'..."
    groupadd -r mama
else
    echo "System group 'mama' already exists."
fi

# Add invoking user to 'mama' and 'kvm' groups if run under sudo
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    echo "Adding user '$SUDO_USER' to groups 'mama' and 'kvm'..."
    usermod -aG mama,kvm "$SUDO_USER"
fi

# 2. Directory Layout Setup in /usr/share/mama
echo "Setting up directory structure in /usr/share/mama..."
mkdir -p /usr/share/mama/{machines,os,os-descriptions,jobs,tftp,log,locks}

# 3. Targeted Non-Recursive Permissions
# Allow group 'mama' to manage SQLite and logs, while preserving rootfs files in machines/ and os/
echo "Applying non-recursive permissions on /usr/share/mama..."
chgrp mama /usr/share/mama /usr/share/mama/log /usr/share/mama/locks
chmod 2775 /usr/share/mama /usr/share/mama/log /usr/share/mama/locks

# Ensure database file exists and is group-writable by group 'mama'
touch /usr/share/mama/mama.db
chgrp mama /usr/share/mama/mama.db* 2>/dev/null || true
chmod 0664 /usr/share/mama/mama.db* 2>/dev/null || true

if [ -d /usr/share/mama/log ]; then
    chgrp -R mama /usr/share/mama/log
    chmod 2775 /usr/share/mama/log
    find /usr/share/mama/log -type f -exec chmod 0664 {} + 2>/dev/null || true
fi

# 4. QEMU Bridge Helper Configuration
echo "Configuring QEMU bridge helper permissions for br0..."
mkdir -p /etc/qemu
if ! grep -q "allow br0" /etc/qemu/bridge.conf 2>/dev/null; then
    echo "allow br0" >> /etc/qemu/bridge.conf
    chmod 0644 /etc/qemu/bridge.conf
fi

# 5. Configuration Directory and Default mama.conf Setup
echo "Setting up configuration in /etc/mama..."
mkdir -p /etc/mama
if [ ! -f /etc/mama/mama.conf ]; then
    echo "Creating default /etc/mama/mama.conf..."
    cat <<'EOF' > /etc/mama/mama.conf
# MAMA Central Configuration File
[mama]
# Server IP address and TFTP root
mama_host = 192.168.2.1
mama_path = /usr/share/mama
db_file = /usr/share/mama/mama.db

# DHCP Configuration
dhcp_enabled = true
dhcp_interface = br0
dhcp_range_start = 192.168.2.100
dhcp_range_end = 192.168.2.200
dhcp_netmask = 255.255.255.0
dhcp_lease_time = 12h
# dhcp_mtu = 9000  # Interface MTU (DHCP Option 26, e.g. 1500 or 9000)

# NFS Configuration
nfs_enabled = true
nfs_service_name = nfs-server
nfs_exports_path = /etc/exports.d/mama.exports

# Virtual Machine Defaults (auto: host RAM minus reserve)
vm_default_ram_mb = auto
vm_host_ram_reserve_mb = 4096
# vm_default_cpus = 4
EOF
    chmod 0644 /etc/mama/mama.conf
fi

# 6. Stage Vendor iPXE Firmware Boot Binaries (if installed)
echo "Staging vendor iPXE binaries into TFTP root..."
mkdir -p /usr/share/mama/tftp
if [ -f /usr/share/ipxe/ipxe-x86_64.efi ]; then
    cp -f /usr/share/ipxe/ipxe-x86_64.efi /usr/share/mama/tftp/ipxe.efi
    echo "  -> Copied x86_64 UEFI bootloader (ipxe.efi)"
fi
if [ -f /usr/share/ipxe/undionly.kpxe ]; then
    cp -f /usr/share/ipxe/undionly.kpxe /usr/share/mama/tftp/undionly.kpxe
    echo "  -> Copied BIOS PXE bootloader (undionly.kpxe)"
fi
if [ -f /usr/share/ipxe/snp-arm64.efi ]; then
    cp -f /usr/share/ipxe/snp-arm64.efi /usr/share/mama/tftp/ipxe-arm64.efi
    echo "  -> Copied ARM64 UEFI bootloader (ipxe-arm64.efi)"
fi

# 7. NFS Server Export Configuration
echo "Configuring NFS export in /etc/exports.d/mama.exports..."
mkdir -p /etc/exports.d
cat <<'EOF' > /etc/exports.d/mama.exports
/usr/share/mama 192.168.2.0/24(rw,async,no_root_squash,no_subtree_check,fsid=0)
EOF
chmod 0644 /etc/exports.d/mama.exports

if command -v systemctl >/dev/null 2>&1; then
    echo "Reloading NFS exports..."
    systemctl enable nfs-server 2>/dev/null || true
    systemctl reload-or-restart nfs-server 2>/dev/null || true
fi

# 8. Stage SSH Public Key (for target machine deployment)
if [ ! -f /usr/share/mama/authorized_keys ]; then
    USER_HOME=""
    if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
        USER_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
    fi
    PUB_KEY=""
    for k in "${USER_HOME}/.ssh/id_ed25519.pub" "${USER_HOME}/.ssh/id_rsa.pub" "/root/.ssh/id_ed25519.pub" "/root/.ssh/id_rsa.pub"; do
        if [ -f "$k" ]; then
            PUB_KEY="$k"
            break
        fi
    done
    if [ -n "$PUB_KEY" ]; then
        echo "Staging developer SSH public key ($PUB_KEY) into /usr/share/mama/authorized_keys..."
        cp "$PUB_KEY" /usr/share/mama/authorized_keys
        chmod 0644 /usr/share/mama/authorized_keys
    fi
fi

# 9. Initialize Default Worker Machines (jobs and os-builder)
echo "Initializing default worker machines in database..."
PYTHONPATH="$SCRIPT_DIR/src" python3 -c '
from mama.repository import MachineRepository
import os, pathlib

MachineRepository.init_db()

default_workers = [
    {
        "name": "jobs",
        "mac": "de:ad:be:ef:00:01",
        "arch": "",
        "only_vm": "1",
        "start_timeout": 45,
        "stop_timeout": 10,
    },
    {
        "name": "os-builder",
        "mac": "de:ad:be:ef:00:02",
        "arch": "",
        "only_vm": "1",
        "start_timeout": 240,
        "stop_timeout": 30,
    },
]

locks_dir = pathlib.Path("/usr/share/mama/locks")
locks_dir.mkdir(parents=True, exist_ok=True)

for w in default_workers:
    if not MachineRepository.get_by_name(w["name"]):
        MachineRepository.upsert_machine(w)
        print(f"  -> Created default worker machine: {w[\"name\"]} ({w[\"mac\"]}, VM)")

    lock_file = locks_dir / f"{w[\"name\"]}.lock"
    lock_file.touch(mode=0o664, exist_ok=True)
'
chgrp mama /usr/share/mama/locks/*.lock 2>/dev/null || true
chmod 0664 /usr/share/mama/locks/*.lock 2>/dev/null || true

# 10. Systemd Service Installation
SERVICE_FILE="$SCRIPT_DIR/mama-boot.service"
if [ -f "$SERVICE_FILE" ] && [ -d /etc/systemd/system ]; then
    echo "Installing systemd service mama-boot.service..."
    install -m 0644 "$SERVICE_FILE" /etc/systemd/system/mama-boot.service
    if command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload || true
        systemctl enable --now mama-boot.service || true
        echo "mama-boot.service enabled and started."
    fi
fi

# 11. CLI Runner Symlinks
if [ -f "$SCRIPT_DIR/mama" ]; then
    echo "Linking CLI runner to /usr/local/bin/mama and /usr/bin/mama..."
    mkdir -p /usr/local/bin
    ln -sf "$SCRIPT_DIR/mama" /usr/local/bin/mama
    chmod +x "$SCRIPT_DIR/mama"

    if [ -d /usr/bin ]; then
        ln -sf "$SCRIPT_DIR/mama" /usr/bin/mama 2>/dev/null || true
    fi
fi

echo "=== MAMA Installation Complete ==="
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    echo "NOTE: User '$SUDO_USER' was added to 'mama' and 'kvm' groups."
    echo "      Log out and back in (or run 'newgrp mama && newgrp kvm') for group access to take effect."
fi
