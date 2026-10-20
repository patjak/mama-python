#!/usr/bin/env bash
set -euo pipefail

# MAMA Uninstaller
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

# Remove tmpfiles configuration
rm -f /etc/tmpfiles.d/mama.conf

# Remove CLI symlink
rm -f /usr/local/bin/mama

# Remove runtime directory
rm -rf /run/mama

echo "CLI symlink, runtime state, and systemd service removed."
echo "Note: /var/lib/mama, /var/log/mama, and /usr/share/mama have been preserved."
echo "      To remove them manually, delete those directories."
echo "=== Uninstall Complete ==="
