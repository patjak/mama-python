from __future__ import annotations

import contextlib
import os
import pathlib
import socket
import subprocess
from typing import Union

from rich.console import Console

from . import config
from .logger import error, fatal, out
from .machine import Machine
from .os_mgr import Arch, Os
from .util import Util


@contextlib.contextmanager
def chroot_mounts(root_dir: pathlib.Path):
    """Safely mount procfs and sysfs inside a chroot rootfs and guarantee unmounting on block exit."""
    sudo_prefix = [] if Util.is_root() else ["sudo"]
    proc_dir = root_dir / "proc"
    sys_dir = root_dir / "sys"
    proc_dir.mkdir(parents=True, exist_ok=True)
    sys_dir.mkdir(parents=True, exist_ok=True)

    mounted: list[pathlib.Path] = []
    try:
        res = subprocess.run(sudo_prefix + ["mount", "-t", "proc", "/proc", str(proc_dir)])
        if res.returncode != 0:
            error(f"Failed to mount procfs to {proc_dir}")
            return
        mounted.append(proc_dir)

        res = subprocess.run(sudo_prefix + ["mount", "-t", "sysfs", "sysfs", str(sys_dir)])
        if res.returncode != 0:
            error(f"Failed to mount sysfs to {sys_dir}")
            return
        mounted.append(sys_dir)

        yield
    finally:
        for m in reversed(mounted):
            try:
                res = subprocess.run(sudo_prefix + ["umount", str(m)])
                if res.returncode != 0:
                    error(f"Failed to umount {m}")
            except Exception:
                pass


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

    mama_arch = subprocess.check_output(["uname", "-p"], text=True).strip()
    sudo_prefix = [] if Util.is_root() else ["sudo"]

    if mama_arch == arch and builder_mach.only_vm:
        run_os_build_script(arch, os_name, packages=packages)
    else:
        cmd = sudo_prefix + ["mama", "job", "os-builder", "run", builder_mach.name, arch, os_name]
        if packages:
            cmd.extend(["--args", packages])
        subprocess.run(cmd)


def is_proxy_available(host: str, port: int, timeout: float = 0.5) -> bool:
    """Quick socket check to verify if a package proxy daemon is listening."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def get_kiwi_proxy_args() -> list[str]:
    """Return kiwi --setenv arguments for the package proxy if enabled and reachable."""
    if not getattr(config, "PACKAGE_PROXY_ENABLED", False):
        return []

    proxy_host = getattr(config, "PACKAGE_PROXY_HOST", config.MAMA_HOST)
    proxy_port = getattr(config, "PACKAGE_PROXY_PORT", 3142)
    proxy_url = f"http://{proxy_host}:{proxy_port}"

    if not is_proxy_available(proxy_host, proxy_port):
        out(f"Notice: Package cache proxy at {proxy_url} is unreachable. Proceeding with direct download.")
        return []

    return [
        f"--setenv=http_proxy={proxy_url}",
        f"--setenv=https_proxy={proxy_url}",
        f"--setenv=HTTP_PROXY={proxy_url}",
        f"--setenv=HTTPS_PROXY={proxy_url}",
    ]


def run_os_build_script(arch: str, os_name: str, packages: str = "") -> None:
    """Execute local Kiwi-ng build, mount proc/sys, chroot dracut initramfs generation, and install authorized_keys."""
    packages_list = [f"--add-package={p}" for p in packages.split()]

    desc_path = os.path.join(config.MAMA_PATH, "os-descriptions", "")
    archs = Util.get_directory_contents(desc_path, 1)
    oses = Util.get_directory_contents(desc_path, 2)

    if arch not in archs:
        fatal(f"Invalid architecture: {arch}")

    if f"{arch}/{os_name}" not in oses:
        fatal(f"Invalid os: {arch}/{os_name}")

    target = pathlib.Path(config.MAMA_PATH) / "os" / arch / os_name
    desc = pathlib.Path(config.MAMA_PATH) / "os-descriptions" / arch / os_name
    tmp_dir = pathlib.Path(f"/dev/shm/mama-kiwi-{arch}-{os_name}")
    sudo_prefix = [] if Util.is_root() else ["sudo"]

    with Os.lock(arch, os_name, shared=False):
        subprocess.run(sudo_prefix + ["rm", "-Rf", str(tmp_dir)])

        kiwi_cmd = (
            sudo_prefix
            + ["kiwi-ng"]
            + get_kiwi_proxy_args()
            + [
                "--debug",
                "system",
                "prepare",
                "--description",
                str(desc),
                "--root",
                str(tmp_dir),
            ]
            + packages_list
        )

        console = Console()
        with console.status(f"[bold cyan]Building OS template {arch}/{os_name} with kiwi-ng...[/bold cyan]"):
            out(" ".join(kiwi_cmd))
            res = subprocess.run(kiwi_cmd)

        if res.returncode != 0:
            error("Failed to build os with kiwi")
            subprocess.run(sudo_prefix + ["rm", "-Rf", str(tmp_dir)])
            return

        with console.status("[bold cyan]Mounting procfs and sysfs...[/bold cyan]"):
            pass

        with chroot_mounts(tmp_dir):
            config_files = list((tmp_dir / "boot").glob("config-*"))
            if not config_files:
                error("Failed to find /boot/config-*")
                subprocess.run(sudo_prefix + ["rm", "-Rf", str(tmp_dir)])
                return

            kver = config_files[0].name.split("config-", 1)[1]

            with console.status(f"[bold cyan]Generating initramfs with dracut (kernel {kver})...[/bold cyan]"):
                res = subprocess.run(sudo_prefix + ["chroot", str(tmp_dir), "dracut", "-f", "--kver", kver])

            if res.returncode != 0:
                error("Failed to run dracut -f inside chroot")
                subprocess.run(sudo_prefix + ["rm", "-Rf", str(tmp_dir)])
                return

        res = subprocess.run(sudo_prefix + ["rm", "-Rf", str(target)])
        if res.returncode != 0:
            fatal(f"Failed to remove old version of os: {target}")

        res = subprocess.run(sudo_prefix + ["mkdir", "-p", str(pathlib.Path(config.MAMA_PATH) / "os" / arch)])
        if res.returncode != 0:
            fatal("Failed to create target directory")

        out("Moving OS into place")
        res = subprocess.run(sudo_prefix + ["mv", str(tmp_dir), str(target)])
        if res.returncode != 0:
            error("Failed to store new os build")
            subprocess.run(sudo_prefix + ["rm", "-Rf", str(tmp_dir)])
            return

        initrd_files = list((target / "boot").glob("initrd-*"))
        if initrd_files:
            chmod_args = [str(f) for f in initrd_files]
            res = subprocess.run(sudo_prefix + ["chmod", "644"] + chmod_args)
            if res.returncode != 0:
                fatal("Failed to change permission on initrd")

        auth_keys = pathlib.Path(config.MAMA_PATH) / "authorized_keys"
        res = subprocess.run(sudo_prefix + ["mkdir", "-p", str(target / "root" / ".ssh")])
        if res.returncode == 0 and auth_keys.exists():
            res = subprocess.run(sudo_prefix + ["cp", str(auth_keys), str(target / "root" / ".ssh/")])
            if res.returncode != 0:
                fatal("Failed to copy authorized_keys")

    out("OS build completed")