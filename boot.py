#!/usr/bin/env python3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import pathlib
import shutil
import socket
import struct
import subprocess
import sys
import threading
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


class TFTPServer:
    """Read-only TFTP server supporting RFC 1350 and RFC 2347 option negotiation."""

    def __init__(self, host: str, port: int, root_dir: pathlib.Path) -> None:
        self.host = host
        self.port = port
        self.root_dir = root_dir.resolve()
        self.sock: socket.socket | None = None
        self._running = False

    def start(self) -> threading.Thread | None:
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind((self.host, self.port))
            self._running = True
            sys.stderr.write(f"[boot.py] Starting MAMA TFTP Server on udp://{self.host}:{self.port}...\n")
            thread = threading.Thread(target=self._listen, daemon=True)
            thread.start()
            return thread
        except PermissionError:
            sys.stderr.write(
                f"[boot.py] [TFTP] Error: Permission denied binding to UDP port {self.port}. "
                f"Run with root privileges to serve TFTP on port 69.\n"
            )
        except Exception as e:
            sys.stderr.write(f"[boot.py] [TFTP] Error starting server on {self.host}:{self.port}: {e}\n")
        return None

    def _listen(self) -> None:
        while self._running and self.sock:
            try:
                data, addr = self.sock.recvfrom(2048)
                if not data:
                    continue
                t = threading.Thread(target=self._handle_request, args=(data, addr), daemon=True)
                t.start()
            except Exception as e:
                if self._running:
                    sys.stderr.write(f"[boot.py] [TFTP] Listener error: {e}\n")

    def _handle_request(self, data: bytes, client_addr: tuple[str, int]) -> None:
        if len(data) < 2:
            return

        opcode = struct.unpack(">H", data[:2])[0]
        if opcode != 1:  # Only RRQ (Read Request) is supported
            self._send_error(client_addr, 4, "Illegal TFTP operation (Read-only server)")
            return

        try:
            parts = data[2:].split(b"\x00")
            if len(parts) < 2:
                self._send_error(client_addr, 4, "Malformed RRQ packet")
                return

            raw_filename = parts[0].decode("utf-8", errors="ignore").lstrip("/\\")
            mode = parts[1].decode("utf-8", errors="ignore").lower()

            options: dict[str, str] = {}
            if len(parts) > 2:
                opt_parts = parts[2:]
                for i in range(0, len(opt_parts) - 1, 2):
                    opt_name = opt_parts[i].decode("utf-8", errors="ignore").lower()
                    opt_val = opt_parts[i + 1].decode("utf-8", errors="ignore")
                    if opt_name and opt_val:
                        options[opt_name] = opt_val
        except Exception as e:
            self._send_error(client_addr, 0, f"Failed to parse request: {e}")
            return

        # Check in MAMA_PATH/tftp first, then fallback to MAMA_PATH
        target_file = (self.root_dir / "tftp" / raw_filename).resolve()
        if not target_file.exists() or not target_file.is_file():
            target_file = (self.root_dir / raw_filename).resolve()

        # Prevent directory traversal attacks
        try:
            target_file.relative_to(self.root_dir)
        except ValueError:
            self._send_error(client_addr, 2, "Access violation")
            return

        if not target_file.exists() or not target_file.is_file():
            sys.stderr.write(f"[boot.py] [TFTP] File not found: {raw_filename} requested by {client_addr[0]}\n")
            self._send_error(client_addr, 1, f"File not found: {raw_filename}")
            return

        sys.stderr.write(f"[boot.py] [TFTP] Serving {raw_filename} ({target_file.stat().st_size} bytes) to {client_addr[0]}\n")
        self._transfer_file(target_file, client_addr, options)

    def _transfer_file(self, file_path: pathlib.Path, client_addr: tuple[str, int], options: dict[str, str]) -> None:
        session_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        session_sock.settimeout(5.0)

        file_size = file_path.stat().st_size
        blksize = 512

        oack_opts: dict[str, str] = {}
        if "blksize" in options:
            try:
                requested_blksize = int(options["blksize"])
                blksize = min(max(512, requested_blksize), 1428)
                oack_opts["blksize"] = str(blksize)
            except ValueError:
                pass

        if "tsize" in options:
            oack_opts["tsize"] = str(file_size)

        try:
            with open(file_path, "rb") as f:
                block_num = 1

                if oack_opts:
                    oack_pkt = bytearray(b"\x00\x06")
                    for k, v in oack_opts.items():
                        oack_pkt.extend(k.encode("utf-8") + b"\x00" + v.encode("utf-8") + b"\x00")

                    for attempt in range(5):
                        session_sock.sendto(oack_pkt, client_addr)
                        try:
                            resp, _ = session_sock.recvfrom(512)
                            if len(resp) >= 4:
                                op, ack_blk = struct.unpack(">HH", resp[:4])
                                if op == 4 and ack_blk == 0:
                                    break
                                elif op == 5:
                                    return
                        except socket.timeout:
                            continue
                    else:
                        return

                while True:
                    f.seek((block_num - 1) * blksize)
                    chunk = f.read(blksize)
                    data_pkt = struct.pack(">HH", 3, block_num & 0xFFFF) + chunk

                    ack_received = False
                    for attempt in range(5):
                        session_sock.sendto(data_pkt, client_addr)
                        try:
                            resp, _ = session_sock.recvfrom(512)
                            if len(resp) >= 4:
                                op, ack_blk = struct.unpack(">HH", resp[:4])
                                if op == 4 and ack_blk == (block_num & 0xFFFF):
                                    ack_received = True
                                    break
                                elif op == 5:
                                    return
                        except socket.timeout:
                            continue

                    if not ack_received:
                        sys.stderr.write(f"[boot.py] [TFTP] Transfer timed out for {client_addr[0]}\n")
                        return

                    if len(chunk) < blksize:
                        break

                    block_num += 1

        except Exception as e:
            sys.stderr.write(f"[boot.py] [TFTP] Error transferring file to {client_addr[0]}: {e}\n")
            self._send_error_on_sock(session_sock, client_addr, 0, str(e))
        finally:
            session_sock.close()

    def _send_error(self, client_addr: tuple[str, int], code: int, msg: str) -> None:
        temp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._send_error_on_sock(temp_sock, client_addr, code, msg)
        temp_sock.close()

    def _send_error_on_sock(self, sock: socket.socket, client_addr: tuple[str, int], code: int, msg: str) -> None:
        err_pkt = struct.pack(">HH", 5, code) + msg.encode("utf-8") + b"\x00"
        try:
            sock.sendto(err_pkt, client_addr)
        except Exception:
            pass

    def stop(self) -> None:
        self._running = False
        if self.sock:
            self.sock.close()


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

        if is_safe and target_file.exists() and target_file.is_file():
            self._send_file(target_file)
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
            mac = mac_param[0]
        else:
            mac = get_mac_from_ip(client_ip)

        if not mac:
            response = "#!ipxe\necho No MAC specified\nshell\n"
            self._send_response(200, response)
            return

        Settings.load()

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
    tftp_port = int(os.environ.get("TFTP_PORT", getattr(config, "TFTP_PORT", 69)))
    bind_addr = os.environ.get("BIND_ADDR", config.MAMA_HOST)

    mama_base = pathlib.Path(config.MAMA_PATH).resolve()

    # Start TFTP Server thread
    tftp_server = TFTPServer(bind_addr, tftp_port, mama_base)
    tftp_server.start()

    print(f"Starting MAMA iPXE Server on http://{bind_addr}:{port}...")
    server = ThreadingHTTPServer((bind_addr, port), iPXEHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down servers.")
        tftp_server.stop()
        server.server_close()


if __name__ == "__main__":
    main()