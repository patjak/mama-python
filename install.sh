#!/usr/bin/env bash
set -euo pipefail

# MAMA (Machine Administration & Management Architecture) Installer
# Sets up FHS system directories, permissions, groups, and services.

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

# Add invoking user to 'mama' group if invoked under sudo
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    echo "Adding user '$SUDO_USER' to group 'mama'..."
    usermod -aG mama "$SUDO_USER"
fi

# 2. Persistent State Directory (/var/lib/mama)
echo "Configuring state directory /var/lib/mama..."
mkdir -p /var/lib/mama
chown root:mama /var/lib/mama
chmod 2775 /var/lib/mama

# 3. Log Directory (/var/log/mama)
echo "Configuring log directory /var/log/mama..."
mkdir -p /var/log/mama
chown root:mama /var/log/mama
chmod 2775 /var/log/mama

# 4. Volatile Runtime & Lock Directories (/run/mama)
echo "Configuring runtime directories in /run/mama..."
mkdir -p /run/mama/locks/os
chown -R root:mama /run/mama
chmod -R 2775 /run/mama

# Install systemd tmpfiles configuration to recreate /run/mama on reboot
if [ -f "$SCRIPT_DIR/systemd/mama-tmpfiles.conf" ]; then
    echo "Installing /etc/tmpfiles.d/mama.conf..."
    mkdir -p /etc/tmpfiles.d
    install -m 0644 "$SCRIPT_DIR/systemd/mama-tmpfiles.conf" /etc/tmpfiles.d/mama.conf
    if command -v systemd-tmpfiles >/dev/null 2>&1; then
        systemd-tmpfiles --create /etc/tmpfiles.d/mama.conf || true
    fi
fi

# 5. Served Payload & Template Directories (/usr/share/mama)
echo "Configuring payload directories in /usr/share/mama..."
mkdir -p /usr/share/mama/{machines,os,os-descriptions,jobs,tftp}
chown -R root:root /usr/share/mama
chmod 0755 /usr/share/mama

# 6. Automatic Migration Helper for Existing Installations
if [ -f /usr/share/mama/mama.db ] && [ ! -f /var/lib/mama/mama.db ]; then
    echo "Migrating existing database from /usr/share/mama/mama.db to /var/lib/mama/mama.db..."
    cp -p /usr/share/mama/mama.db* /var/lib/mama/ 2>/dev/null || cp -p /usr/share/mama/mama.db /var/lib/mama/
    chown root:mama /var/lib/mama/mama.db* || true
    chmod 0664 /var/lib/mama/mama.db* || true
    echo "Database migrated successfully."
fi

if [ -d /usr/share/mama/log ] && [ ! -d /var/log/mama/log ]; then
    if [ "$(ls -A /usr/share/mama/log 2>/dev/null)" ]; then
        echo "Copying existing logs to /var/log/mama..."
        cp -rn /usr/share/mama/log/* /var/log/mama/ 2>/dev/null || true
        chown -R root:mama /var/log/mama
    fi
fi

# 7. Systemd Service Installation
SERVICE_FILE="$SCRIPT_DIR/mama-boot.service"
if [ -f "$SERVICE_FILE" ] && [ -d /etc/systemd/system ]; then
    echo "Installing systemd service mama-boot.service..."
    install -m 0644 "$SERVICE_FILE" /etc/systemd/system/mama-boot.service
    if command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload || true
        echo "mama-boot.service installed. Start or enable with: systemctl enable --now mama-boot.service"
    fi
fi

# 8. CLI Runner Setup
if [ -f "$SCRIPT_DIR/mama" ]; then
    echo "Linking CLI to /usr/local/bin/mama..."
    mkdir -p /usr/local/bin
    ln -sf "$SCRIPT_DIR/mama" /usr/local/bin/mama
    chmod +x "$SCRIPT_DIR/mama"

    # Also link into /usr/bin/mama to ensure systemd unit compatibility and overwrite stale links
    if [ -d /usr/bin ]; then
        echo "Linking CLI to /usr/bin/mama..."
        ln -sf "$SCRIPT_DIR/mama" /usr/bin/mama 2>/dev/null || true
    fi
fi

echo "=== MAMA Installation Complete ==="
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    echo "NOTE: User '$SUDO_USER' was added to the 'mama' group."
    echo "      Log out and back in (or run 'newgrp mama') for group access to take effect."
fi
