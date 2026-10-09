from __future__ import annotations

import os
import subprocess
import urllib.parse
from typing import Any, Callable, Dict, List


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


def application(environ: Dict[str, Any], start_response: Callable[..., Any]) -> List[bytes]:
    """
    WSGI Application entry point porting boot.php.
    Serves dynamic iPXE scripts and boot configuration to PXE clients.
    """
    query_string = environ.get("QUERY_STRING", "")
    params = urllib.parse.parse_qs(query_string)

    server_ip = (
        environ.get("SERVER_ADDR")
        or environ.get("HTTP_HOST", "").split(":")[0]
        or environ.get("SERVER_NAME", "127.0.0.1")
    )
    client_ip = environ.get("REMOTE_ADDR", "")

    # Retrieve MAC from GET parameters or resolve via neighbor table
    mac_param = params.get("MAC") or params.get("mac")
    if mac_param and mac_param[0]:
        mac = mac_param[0]
    else:
        mac = get_mac_from_ip(client_ip)

    if not mac:
        body = "#!ipxe\necho No MAC specified\nshell\n"
        start_response("200 OK", [("Content-Type", "text/plain; charset=utf-8")])
        return [body.encode("utf-8")]

    # Check for BOOTINFO query parameter
    is_bootinfo = "BOOTINFO" in params or "bootinfo" in params
    action = "bootinfo" if is_bootinfo else "ipxe"

    cmd = ["/usr/bin/mama", action, mac, server_ip]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        output = res.stdout
    except Exception as e:
        output = f"Error running mama command: {e}\n"

    start_response("200 OK", [("Content-Type", "text/plain; charset=utf-8")])
    return [output.encode("utf-8")]


if __name__ == "__main__":
    from wsgiref.simple_server import make_server

    port = int(os.environ.get("PORT", 8080))
    print(f"Serving boot handler on http://0.0.0.0:{port}...")
    httpd = make_server("0.0.0.0", port, application)
    httpd.serve_forever()