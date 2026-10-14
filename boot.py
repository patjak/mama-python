#!/usr/bin/env python3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import pathlib
import shutil
import sys
import urllib.parse

# Ensure MAMA path is in sys.path
script_dir = pathlib.Path(__file__).parent.resolve()
sys.path.insert(0, str(script_dir))

import config
from dhcp import DnsmasqManager
from machine import Machine
from main import bootinfo
from repository import MachineRepository


def clean_mac(mac_str: str) -> str:
    """Normalize MAC address by stripping whitespace, leading slashes/backslashes, and converting hyphens to colons."""
    if not mac_str:
        return ""
    return mac_str.strip().lstrip("\\/\"'").replace("-", ":").lower()


def get_mac_from_ip(client_ip: str) -> str:
    """Extract client MAC address from DHCP leases, MachineRepository, or neighbor ARP table."""
    if not client_ip:
        return ""

    # 1. Check active dnsmasq DHCP leases
    if getattr(config, "DHCP_ENABLED", True):
        leases = DnsmasqManager.get_active_leases()
        for mac, ip in leases.items():
            if ip == client_ip:
                return clean_mac(mac)

    # 2. Check registered machines in MAMA database
    try:
        for m in MachineRepository.get_all():
            if m.get("ip") == client_ip and m.get("mac"):
                return clean_mac(m.get("mac"))
    except Exception:
        pass

    # 3. Fallback to neighbor ARP table
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


class iPXEHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        url_path = parsed_url.path

        mama_base = pathlib.Path(config.MAMA_PATH).resolve()

        clean_path = url_path
        if clean_path.startswith("/mama/"):
            clean_path = clean_path[len("/mama/") :]
        elif clean_path.startswith("/"):
            clean_path = clean_path[1:]

        target_file = (mama_base / clean_path).resolve()

        is_safe = False
        try:
            target_file.relative_to(mama_base)
            is_safe = True
        except ValueError:
            is_safe = False

        # Exclude /boot.py so dynamic iPXE script handling is never bypassed
        if clean_path != "boot.py" and is_safe and target_file.exists() and target_file.is_file():
            mach_name = ""
            client_mac = ""
            fname = target_file.name
            try:
                parts = pathlib.Path(clean_path).parts
                if len(parts) >= 2 and parts[0] == "machines":
                    mach_name = parts[1]
                    if fname.startswith("initrd"):
                        Machine.set_state_by_name(mach_name, "fetching-initrd")
                    elif fname.startswith(("vmlinuz", "Image", "vmlinux")):
                        Machine.set_state_by_name(mach_name, "fetching-kernel")
                    else:
                        Machine.set_state_by_name(mach_name, f"fetching-{fname}")
                else:
                    client_ip = self.client_address[0]
                    client_mac = clean_mac(get_mac_from_ip(client_ip))
                    if client_mac:
                        if fname.startswith("initrd"):
                            Machine.set_state_by_mac(client_mac, "fetching-initrd")
                        elif fname.startswith(("vmlinuz", "Image", "vmlinux")):
                            Machine.set_state_by_mac(client_mac, "fetching-kernel")
                        else:
                            Machine.set_state_by_mac(client_mac, f"fetching-{fname}")
            except Exception as e:
                sys.stderr.write(f"[boot.py] Error updating state for static file transfer: {e}\n")

            self._send_file(target_file)

            # Transition state to 'booting' once kernel / initrd file transfer finishes
            if fname.startswith(("initrd", "vmlinuz", "Image", "vmlinux")):
                try:
                    if mach_name:
                        Machine.set_state_by_name(mach_name, "booting")
                    elif client_mac:
                        Machine.set_state_by_mac(client_mac, "booting")
                except Exception as e:
                    sys.stderr.write(f"[boot.py] Error updating state to booting: {e}\n")

            return

        params = urllib.parse.parse_qs(parsed_url.query)

        host_header = self.headers.get("Host", "")
        server_ip = host_header.split(":")[0] if host_header else config.MAMA_HOST

        if host_header and ":" in host_header:
            try:
                server_port = int(host_header.split(":")[1])
            except ValueError:
                server_port = getattr(self.server, "server_port", int(os.environ.get("PORT", 8080)))
        else:
            server_port = getattr(self.server, "server_port", int(os.environ.get("PORT", 8080)))

        client_ip = self.client_address[0]

        mac_param = params.get("MAC") or params.get("mac")
        if mac_param and mac_param[0]:
            mac = clean_mac(mac_param[0])
        else:
            mac = clean_mac(get_mac_from_ip(client_ip))

        if not mac:
            response = "#!ipxe\necho No MAC specified\nshell\n"
            self._send_response(200, response)
            return

        # Track that machine requested its iPXE config and sync lease to DB
        try:
            Machine.set_state_by_mac(mac, "ipxe-boot-config")
            if getattr(config, "DHCP_ENABLED", True):
                DnsmasqManager.sync_leases_to_db()
        except Exception as e:
            sys.stderr.write(f"[boot.py] Error setting state for MAC {mac}: {e}\n")

        MachineRepository.load()

        try:
            kernel, initrd, append = bootinfo(mac, server_ip, server_port)
            response = f"#!ipxe\ninitrd {initrd} ||\nkernel {kernel} {append} ||\nboot || shell\n"
        except SystemExit:
            response = "#!ipxe\necho Machine not configured or skipping iPXE\nexit 1\n"
        except Exception as e:
            response = f"#!ipxe\necho Error generating boot config: {e}\nshell\n"

        self._send_response(200, response)

    def _send_file(self, file_path: pathlib.Path) -> None:
        try:
            file_size = file_path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(file_size))
            self.end_headers()

            with open(file_path, "rb") as f:
                shutil.copyfileobj(f, self.wfile)
        except Exception as e:
            self.send_error(500, f"Error reading file: {e}")

    def _send_response(self, status_code: int, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write(f"[boot.py] {self.address_string()} - {format % args}\n")


def main() -> None:
    port = int(os.environ.get("PORT", 8080))
    bind_addr = os.environ.get("BIND_ADDR", config.MAMA_HOST)

    # Initialize repository database on server startup
    MachineRepository.load()

    # Synchronize dnsmasq DHCP, leases, and built-in TFTP server
    if getattr(config, "DHCP_ENABLED", True):
        sys.stderr.write("[boot.py] Synchronizing dnsmasq DHCP/TFTP server...\n")
        DnsmasqManager.reload_or_restart()
        DnsmasqManager.sync_leases_to_db()
    else:
        DnsmasqManager.generate_autoexec_ipxe()

    print(f"Starting MAMA iPXE Server on http://{bind_addr}:{port}...")
    server = ThreadingHTTPServer((bind_addr, port), iPXEHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")
        server.server_close()


if __name__ == "__main__":
    main()