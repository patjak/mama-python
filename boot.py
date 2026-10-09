#!/usr/bin/env python3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import pathlib
import shutil
import subprocess
import sys
import urllib.parse

# Ensure MAMA path is in sys.path
script_dir = pathlib.Path(__file__).parent.resolve()
sys.path.insert(0, str(script_dir))

import config
from main import bootinfo
from services import Settings


def get_mac_from_ip(client_ip: str) -> str:
    """Extract client MAC address from neighbor ARP table given client IP."""
    if not client_ip:
        return ""
    try:
        cmd = f"ip -4 neigh | grep {client_ip}"
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            first_line = res.stdout.strip().splitlines()[0]
            parts = [p for p in first_line.split() if p]
            if len(parts) >= 5:
                return parts[4]
    except Exception:
        pass
    return ""


class iPXEHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        url_path = parsed_url.path

        # 1. Check if the request is for a static file (kernel/initrd under MAMA_PATH)
        mama_base = pathlib.Path(config.MAMA_PATH).resolve()

        clean_path = url_path
        if clean_path.startswith("/mama/"):
            clean_path = clean_path[len("/mama/"):]
        elif clean_path.startswith("/"):
            clean_path = clean_path[1:]

        target_file = (mama_base / clean_path).resolve()

        # Prevent directory traversal attacks and check if file exists
        is_safe = False
        try:
            target_file.relative_to(mama_base)
            is_safe = True
        except ValueError:
            is_safe = False

        if is_safe and target_file.exists() and target_file.is_file():
            self._send_file(target_file)
            return

        # 2. Otherwise, treat as an iPXE dynamic script request
        params = urllib.parse.parse_qs(parsed_url.query)

        host_header = self.headers.get("Host", "")
        server_ip = host_header.split(":")[0] if host_header else config.MAMA_HOST
        client_ip = self.client_address[0]

        mac_param = params.get("MAC") or params.get("mac")
        if mac_param and mac_param[0]:
            mac = mac_param[0]
        else:
            mac = get_mac_from_ip(client_ip)

        if not mac:
            response = "#!ipxe\necho No MAC specified\nshell\n"
            self._send_response(200, response)
            return

        Settings.load()

        try:
            kernel, initrd, append = bootinfo(mac, server_ip)
            response = (
                f"#!ipxe\n"
                f"initrd {initrd} ||\n"
                f"kernel {kernel} {append} ||\n"
                f"boot || shell\n"
            )
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

    print(f"Starting MAMA iPXE Server on http://{bind_addr}:{port}...")
    server = ThreadingHTTPServer((bind_addr, port), iPXEHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")
        server.server_close()


if __name__ == "__main__":
    main()