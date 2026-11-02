#!/usr/bin/env bash
set -euo pipefail

# MAMA (Machine Administration & Management Architecture) Installer
# Configures system groups, non-recursive permissions on /usr/share/mama,
# systemd boot service, and CLI symlinks.

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

# QEMU Bridge Helper Configuration
echo "Configuring QEMU bridge helper permissions for br0..."
mkdir -p /etc/qemu
if ! grep -q "allow br0" /etc/qemu/bridge.conf 2>/dev/null; then
    echo "allow br0" >> /etc/qemu/bridge.conf
    chmod 0644 /etc/qemu/bridge.conf
fi

# Configuration Directory and Default mama.conf Setup
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

# 3. Targeted Non-Recursive Permissions
# Allow group 'mama' to manage SQLite and logs, while preserving rootfs files in machines/ and os/
echo "Applying non-recursive permissions on /usr/share/mama..."
chgrp mama /usr/share/mama /usr/share/mama/log /usr/share/mama/locks
chmod 2775 /usr/share/mama /usr/share/mama/log /usr/share/mama/locks

# Ensure existing database files and logs are readable and writable by group 'mama'
if compgen -G "/usr/share/mama/mama.db*" >/dev/null; then
    chgrp mama /usr/share/mama/mama.db*
    chmod 0664 /usr/share/mama/mama.db*
fi

if [ -d /usr/share/mama/log ]; then
    chgrp -R mama /usr/share/mama/log
    chmod 2775 /usr/share/mama/log
    find /usr/share/mama/log -type f -exec chmod 0664 {} +
fi

# 4. Systemd Service Installation
SERVICE_FILE="$SCRIPT_DIR/mama-boot.service"
if [ -f "$SERVICE_FILE" ] && [ -d /etc/systemd/system ]; then
    echo "Installing systemd service mama-boot.service..."
    install -m 0644 "$SERVICE_FILE" /etc/systemd/system/mama-boot.service
    if command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload || true
        echo "mama-boot.service installed. Start or enable with: systemctl enable --now mama-boot.service"
    fi
fi

# 5. CLI Runner Symlinks
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
    echo "NOTE: User '$SUDO_USER' was added to the 'mama' group."
    echo "      Log out and back in (or run 'newgrp mama') for group access to take effect."
fi
