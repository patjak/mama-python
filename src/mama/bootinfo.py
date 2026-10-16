from __future__ import annotations

import os
from typing import Optional, Tuple, Union

from .logger import fatal
from .machine import Machine
from .repository import MachineRepository


def bootinfo(mac: str, server_ip: str, port: Optional[Union[int, str]] = None) -> Tuple[str, str, str]:
    """Resolve kernel, initrd, and boot command-line append arguments for a given MAC address."""
    mach = Machine.find_by_mac(mac)
    if not mach:
        new_mach = Machine(name=mac, mac=mac, only_vm=False)
        MachineRepository.add_machine(new_mach)
        fatal(f"Machine is not configured in Mama! Added as new machine: {mac}")

    if mach.os == "NONE/NONE":
        fatal("#!pxe\necho Skipping ipxe. Using next boot option.\nexit 1")

    clean_ip = server_ip
    if ":" in server_ip:
        parts = server_ip.split(":", 1)
        clean_ip = parts[0]
        if not port:
            port = parts[1]

    if port is None:
        port = os.environ.get("PORT", 8080)

    http_host = f"{clean_ip}:{port}" if port and str(port) != "80" else clean_ip

    path = f"http://{http_host}/mama/machines/{mach.name}/{mach.os}"
    root = f"root=nfs:{clean_ip}:/machines/{mach.name}/{mach.os},rw,vers=4,nconnect=16"

    console = "console=tty0 console=ttyS0" if mach.is_vm() else ""
    net = f"rd.neednet=1 systemd.hostname={mach.name} ip=dhcp ifname=lan0:{mac}"

    arch = mach.os.split("/")[0] if "/" in mach.os else mach.os
    if arch == "aarch64":
        kernel_filename = f"Image-{mach.kernel}"
    elif arch == "ppc64le":
        kernel_filename = f"vmlinux-{mach.kernel}"
    else:
        kernel_filename = f"vmlinuz-{mach.kernel}"

    initrd_filename = f"initrd-{mach.kernel}"
    params = f"{root} {console} {net} {mach.boot_params}".strip()

    kernel = f"{path}/boot/{kernel_filename}"
    initrd = f"{path}/boot/{initrd_filename}"
    append = f"{params} initrd={initrd_filename}"

    return kernel, initrd, append