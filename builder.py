from __future__ import annotations

import os
import pathlib
import subprocess
from typing import Union

from rich.console import Console

import config
from logger import error, fatal, out
from machine import Machine
from os_mgr import Arch, Os
from repository import Util


def build_os(
    arch: str,
    os_name: str,
    builder: Union[Machine, str] = "os-builder",
    packages: str = "",
) -> None:
    """Build a deployable OS locally or delegate to a designated worker/builder machine."""
    if isinstance(builder, Machine):
        builder_mach = builder
    else:
        builder_mach = Machine.get_by_name(str(builder)) or Machine.find_by_mac(str(builder))

    if not isinstance(builder_mach, Machine):
        fatal(f"Invalid builder machine: {builder}")

    if not Arch.is_buildable(arch):
        fatal(f"Invalid architecture: {arch}")

    if not Os.is_buildable(arch, os_name):
        fatal(f"Invalid os: {arch}/{os_name}")

    mama_arch = subprocess.check_output("uname -p", shell=True, text=True).strip()
    need_sudo = "" if Util.is_root() else "sudo"

    if mama_arch == arch and builder_mach.is_only_vm():
        run_os_build_script(arch, os_name, packages=packages)
    else:
        pkg_arg = f' --args="{packages}"' if packages else ""
        subprocess.run(
            f"{need_sudo} mama job os-builder run {builder_mach.name} {arch} {os_name}{pkg_arg}",
            shell=True,
        )


def run_os_build_script(arch: str, os_name: str, packages: str = "") -> None:
    """Execute local Kiwi-ng build, mount proc/sys, chroot dracut initramfs generation, and install authorized_keys."""
    packages_str = ""
    for package in packages.split():
        packages_str += f" --add-package={package}"

    if not Util.is_root():
        out("You must be root to run this command")

    desc_path = os.path.join(config.MAMA_PATH, "os-descriptions", "")
    archs = Util.get_directory_contents(desc_path, 1)
    oses = Util.get_directory_contents(desc_path, 2)

    if arch not in archs:
        fatal(f"Invalid architecture: {arch}")

    if f"{arch}/{os_name}" not in oses:
        fatal(f"Invalid os: {arch}/{os_name}")

    target = pathlib.Path(config.MAMA_PATH) / "os" / arch / os_name
    desc = pathlib.Path(config.MAMA_PATH) / "os-descriptions" / arch / os_name
    need_sudo = "" if Util.is_root() else "sudo"
    tmp_dir = pathlib.Path(f"/dev/shm/mama-kiwi-{arch}-{os_name}")

    with Os.lock(arch, os_name, shared=False):
        subprocess.run(f"{need_sudo} rm -Rf {tmp_dir}", shell=True)

        console = Console()
        with console.status(f"[bold cyan]Building OS template {arch}/{os_name} with kiwi-ng...[/bold cyan]"):
            cmd = f"{need_sudo} kiwi-ng --debug system prepare --description {desc} --root {tmp_dir}{packages_str}"
            out(cmd)
            res = subprocess.run(cmd, shell=True)

        if res.returncode != 0:
            error("Failed to build os with kiwi")
            subprocess.run(f"{need_sudo} rm -Rf {tmp_dir}", shell=True)
            return

        with console.status("[bold cyan]Mounting procfs and sysfs...[/bold cyan]"):
            res = subprocess.run(f"{need_sudo} mount -t proc /proc {tmp_dir / 'proc'}", shell=True)
            if res.returncode != 0:
                error(f"Failed to mount procfs to {tmp_dir / 'proc'}")
                subprocess.run(f"{need_sudo} rm -Rf {tmp_dir}", shell=True)
                return

            res = subprocess.run(f"{need_sudo} mount -t sysfs sysfs {tmp_dir / 'sys'}", shell=True)
            if res.returncode != 0:
                error(f"Failed to mount sysfs to {tmp_dir / 'sys'}")
                subprocess.run(f"{need_sudo} umount {tmp_dir / 'proc'}", shell=True)
                subprocess.run(f"{need_sudo} rm -Rf {tmp_dir}", shell=True)
                return

        res_k = subprocess.run(f"ls {tmp_dir / 'boot' / 'config-*'}", shell=True, capture_output=True, text=True)
        if res_k.returncode != 0:
            error("Failed to find /boot/config-*")
            subprocess.run(f"{need_sudo} umount {tmp_dir / 'sys'}", shell=True)
            subprocess.run(f"{need_sudo} umount {tmp_dir / 'proc'}", shell=True)
            subprocess.run(f"{need_sudo} rm -Rf {tmp_dir}", shell=True)
            return

        kver = res_k.stdout.strip().split("config-")[1].splitlines()[0]

        with console.status(f"[bold cyan]Generating initramfs with dracut (kernel {kver})...[/bold cyan]"):
            res = subprocess.run(f"{need_sudo} chroot {tmp_dir} dracut -f --kver {kver}", shell=True)

        if res.returncode != 0:
            error("Failed to run dracut -f inside chroot")
            subprocess.run(f"{need_sudo} umount {tmp_dir / 'sys'}", shell=True)
            subprocess.run(f"{need_sudo} umount {tmp_dir / 'proc'}", shell=True)
            subprocess.run(f"{need_sudo} rm -Rf {tmp_dir}", shell=True)
            return

        res = subprocess.run(f"{need_sudo} umount {tmp_dir / 'sys'}", shell=True)
        if res.returncode != 0:
            fatal(f"Failed to umount {tmp_dir / 'sys'}")

        res = subprocess.run(f"{need_sudo} umount {tmp_dir / 'proc'}", shell=True)
        if res.returncode != 0:
            fatal(f"Failed to umount {tmp_dir / 'proc'}")

        res = subprocess.run(f"{need_sudo} rm -Rf {target}", shell=True)
        if res.returncode != 0:
            fatal("Failed to remove old version of os: {target}")

        res = subprocess.run(f"{need_sudo} mkdir -p {pathlib.Path(config.MAMA_PATH) / 'os' / arch}", shell=True)
        if res.returncode != 0:
            fatal("Failed to create target directory")

        out("Moving OS into place")
        res = subprocess.run(f"{need_sudo} mv {tmp_dir} {target}", shell=True)
        if res.returncode != 0:
            error("Failed to store new os build")
            subprocess.run(f"{need_sudo} rm -Rf {tmp_dir}", shell=True)
            return

        res = subprocess.run(f"{need_sudo} chmod 644 {target / 'boot' / 'initrd-*'}", shell=True)
        if res.returncode != 0:
            fatal("Failed to change permission on initrd")

        auth_keys = pathlib.Path(config.MAMA_PATH) / "authorized_keys"
        res = subprocess.run(
            f"{need_sudo} mkdir -p {target / 'root' / '.ssh'} && {need_sudo} cp {auth_keys} {target / 'root' / '.ssh/'}",
            shell=True,
        )
        if res.returncode != 0:
            fatal("Failed to copy authorized_keys")

    out("OS build completed")