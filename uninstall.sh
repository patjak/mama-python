#!/usr/bin/env bash
set -euo pipefail

# MAMA (Machine Administration & Management Architecture) Uninstaller
if [ "$(id -u)" -ne 0 ]; then
    echo "Error: uninstall.sh must be run as root (e.g. sudo ./uninstall.sh)" >&2
    exit 1
fi

echo "=== Uninstalling MAMA ==="

# Stop and remove systemd service
if command -v systemctl >/dev/null 2>&1; then
    systemctl stop mama-boot.service 2>/dev/null || true
    systemctl disable mama-boot.service 2>/dev/null || true
fi
rm -f /etc/systemd/system/mama-boot.service
if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload || true
fi

# Remove CLI symlinks
rm -f /usr/local/bin/mama
if [ -L /usr/bin/mama ]; then
    rm -f /usr/bin/mama
fi

echo "CLI symlinks and systemd service removed."
echo "Note: Payload and database in /usr/share/mama have been preserved."
echo "=== Uninstall Complete ==="
