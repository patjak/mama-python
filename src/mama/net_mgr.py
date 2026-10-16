from __future__ import annotations

import pathlib
import socket
import subprocess
from typing import Optional, Union

import config
from util import Util


def clean_mac(mac_str: str) -> str:
    """Normalize MAC address by stripping whitespace and converting hyphens to colons."""
    if not mac_str:
        return ""
    return mac_str.strip().lstrip("\\/\"'").replace("-", ":").lower()


def get_mac_from_ip(client_ip: str) -> str:
    """Extract client MAC address from DHCP leases, MachineRepository, or the neighbor ARP table."""
    if not client_ip:
        return ""

    if getattr(config, "DHCP_ENABLED", True):
        try:
            from dhcp import DnsmasqManager
            leases = DnsmasqManager.get_active_leases()
            for mac, ip in leases.items():
                if ip == client_ip:
                    return clean_mac(mac)
        except Exception:
            pass

    try:
        from repository import MachineRepository
        for m in MachineRepository.get_all():
            if m.get("ip") == client_ip and m.get("mac"):
                return clean_mac(m.get("mac"))
    except Exception:
        pass

    try:
        with open("/proc/net/arp", "r", encoding="utf-8") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if (
                    len(parts) >= 4
                    and parts[0] == client_ip
                    and parts[3] != "00:00:00:00:00:00"
                ):
                    return clean_mac(parts[3])
    except OSError:
        pass

    return ""


def get_ip_from_leases(mac: str) -> Optional[str]:
    """Look up active IP assigned to a MAC address in dnsmasq leases."""
    if not getattr(config, "DHCP_ENABLED", True):
        return None
    try:
        from dhcp import DnsmasqManager
        leases = DnsmasqManager.get_active_leases()
        return leases.get(clean_mac(mac))
    except Exception:
        return None


def get_ip_from_mac(mac: str) -> Optional[str]:
    """Resolve IP address matching a MAC from /proc/net/arp."""
    target_mac = clean_mac(mac)
    try:
        with open("/proc/net/arp", "r", encoding="utf-8") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if (
                    len(parts) >= 4
                    and clean_mac(parts[3]) == target_mac
                    and parts[3] != "00:00:00:00:00:00"
                ):
                    ip = parts[0]
                    if Util.is_valid_ip(ip):
                        return ip
    except OSError:
        pass
    return None


def is_pingable(ip: str) -> bool:
    """Check ICMP reachability with a 2-second timeout."""
    if not ip:
        return False
    try:
        res = subprocess.run(
            ["ping", "-w", "2", "-c", "1", ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return res.returncode == 0
    except OSError:
        return False


def get_ssh_status(ip: str) -> str:
    """Test SSH connectivity using a standard non-blocking TCP socket check."""
    if not ip:
        return "closed"
    try:
        with socket.create_connection((ip, 22), timeout=3):
            return "open"
    except OSError:
        return "closed"


def is_nfs_mounted(ip: str) -> bool:
    """Check if the client has an active NFS session on the MAMA host."""
    if not ip:
        return False

    nfsd_clients = pathlib.Path("/proc/fs/nfsd/clients")
    if not nfsd_clients.exists() or not nfsd_clients.is_dir():
        return False

    try:
        for client_dir in nfsd_clients.iterdir():
            info_file = client_dir / "info"
            if info_file.is_file():
                try:
                    content = info_file.read_text(encoding="utf-8")
                    if f'"{ip}:' in content:
                        return True
                except OSError:
                    continue
    except (OSError, PermissionError):
        pass

    return False


def run_ssh_cmd(
    ip: str,
    cmd: str,
    timeout_mins: int = 1,
    no_timeout: bool = False,
    logfile: Union[str, bool] = False,
) -> int:
    """Execute command on remote machine via SSH."""
    ssh_args = [
        "ssh",
        "-q",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=no",
        f"root@{ip}",
        "-t",
        cmd,
    ]

    if not no_timeout:
        ssh_args = ["timeout", "--foreground", f"{timeout_mins}m"] + ssh_args

    if logfile:
        with open(logfile, "a", encoding="utf-8") as lf:
            res = subprocess.run(ssh_args, stdout=lf, stderr=subprocess.STDOUT)
    else:
        res = subprocess.run(ssh_args)

    return res.returncode