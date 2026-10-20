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

# Add invoking user to 'mama' group if run under sudo
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    echo "Adding user '$SUDO_USER' to group 'mama'..."
    usermod -aG mama "$SUDO_USER"
fi

# 2. Directory Layout Setup in /usr/share/mama
echo "Setting up directory structure in /usr/share/mama..."
mkdir -p /usr/share/mama/{machines,os,os-descriptions,jobs,tftp,log}

# 3. Targeted Non-Recursive Permissions
# Allow group 'mama' to manage SQLite and logs, while preserving rootfs files in machines/ and os/
echo "Applying non-recursive permissions on /usr/share/mama..."
chgrp mama /usr/share/mama /usr/share/mama/log
chmod 2775 /usr/share/mama /usr/share/mama/log

# Ensure existing database files are readable and writable by group 'mama'
if compgen -G "/usr/share/mama/mama.db*" >/dev/null; then
    chgrp mama /usr/share/mama/mama.db*
    chmod 0664 /usr/share/mama/mama.db*
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
