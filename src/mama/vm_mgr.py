from __future__ import annotations

import os
import pathlib
import shlex
import signal
import subprocess
import time
from typing import TYPE_CHECKING, Optional

from . import config
from .repository import MachineRepository
from .util import Util

if TYPE_CHECKING:
    from .machine import Machine


def get_vmlog_path(machine_name: str) -> pathlib.Path:
    """Return path to this machine's VM serial console log file."""
    log_dir = pathlib.Path(getattr(config, "LOG_DIR", f"{config.MAMA_PATH}/log"))
    return log_dir / f"{machine_name}-vmlog"


def get_vmpid_path(machine_name: str) -> pathlib.Path:
    """Return path to this machine's VM pidfile."""
    run_dir = pathlib.Path(getattr(config, "RUN_DIR", "/run/mama"))
    run_mama = run_dir / f"{machine_name}.pid"
    fallback = pathlib.Path(config.MAMA_PATH) / "run" / f"{machine_name}.pid"

    if run_mama.exists():
        return run_mama
    if fallback.exists():
        return fallback

    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_mama
    except OSError:
        try:
            (pathlib.Path(config.MAMA_PATH) / "run").mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return fallback


def get_vm_pid(machine_name: str) -> Optional[int]:
    """Return active QEMU PID for this machine if running, or None."""
    pid_file = get_vmpid_path(machine_name)
    if not pid_file.exists():
        return None

    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None

    if pid <= 0:
        return None

    try:
        os.kill(pid, 0)
        return pid
    except PermissionError:
        return pid
    except (ProcessLookupError, OSError):
        try:
            pid_file.unlink()
        except OSError:
            pass
        return None


def kill_vm(machine_name: str) -> bool:
    """Terminate active QEMU VM process using SIGTERM and SIGKILL fallback."""
    pid = get_vm_pid(machine_name)
    if not pid:
        return False

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError:
        subprocess.run(["sudo", "kill", str(pid)])

    for _ in range(10):
        try:
            os.kill(pid, 0)
            time.sleep(0.2)
        except (ProcessLookupError, OSError):
            break
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            subprocess.run(["sudo", "kill", "-9", str(pid)])

    pid_file = get_vmpid_path(machine_name)
    try:
        pid_file.unlink(missing_ok=True)
    except OSError:
        subprocess.run(["sudo", "rm", "-f", str(pid_file)])

    return True


def start_vm(mach: Machine) -> bool:
    """Launch virtual instance of machine using QEMU."""
    if not Util.is_root():
        mach.out("You must be root to start a VM")

    if not mach.os:
        mach.error("No OS has been configured!")
        return False

    if not mach.kernel and not mach.os.endswith("/NONE"):
        mach.error("No kernel set for machine")
        return False

    if get_vm_pid(mach.name) is not None:
        mach.error("Failed to start VM. Already running.")
        return False

    vmlog_path = get_vmlog_path(mach.name)
    pid_path = get_vmpid_path(mach.name)

    try:
        vmlog_path.parent.mkdir(parents=True, exist_ok=True)
        if Util.is_root():
            vmlog_path.touch(mode=0o666, exist_ok=True)
            vmlog_path.chmod(0o666)
        else:
            subprocess.run(["sudo", "touch", str(vmlog_path)])
            subprocess.run(["sudo", "chmod", "666", str(vmlog_path)])
        with open(vmlog_path, "w") as f:
            pass
    except Exception as e:
        mach.debug(f"Could not prepare vmlog file {vmlog_path}: {e}")

    try:
        pid_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        mach.debug(f"Could not prepare pidfile directory {pid_path.parent}: {e}")

    mach.is_started = True
    mach.set_state("starting-vm")
    MachineRepository.update_fields(mach.name, {"_is_started": 1})

    mach.out(f"Starting virtual machine with OS {mach.os} and kernel {mach.kernel}")

    num_cores = os.cpu_count() or 1
    os_arch = mach.os.split("/")[0] if "/" in mach.os else mach.os
    mama_arch = subprocess.check_output(["uname", "-p"], text=True).strip()

    if os_arch == "aarch64":
        kernel_filename = f"Image-{mach.kernel}"
    elif os_arch == "ppc64le":
        kernel_filename = f"vmlinux-{mach.kernel}"
    else:
        kernel_filename = f"vmlinuz-{mach.kernel}"
    initrd_filename = f"initrd-{mach.kernel}"

    kernel = os.path.join(mach.get_kernel_path(), kernel_filename)
    initrd = os.path.join(mach.get_kernel_path(), initrd_filename)

    arch = "x86_64" if os_arch == "i686" else os_arch

    qemu_cmd = []
    if not Util.is_root():
        qemu_cmd.append("sudo")
    qemu_cmd.append(f"qemu-system-{arch}")

    qemu_cmd.extend([
        "-daemonize",
        "-pidfile", str(pid_path),
        "-smp", str(num_cores),
        "-m", str(1024 * 44),
        "-net", f"nic,model=virtio,macaddr={mach.mac}",
        "-net", "bridge,br=br0",
        "-display", "none",
        "-chardev", f"file,id=char0,path={vmlog_path}",
        "-serial", "chardev:char0",
        "-monitor", "none",
    ])

    if os_arch == "aarch64":
        qemu_cmd.extend([
            "-machine", "virt,gic-version=3",
            "-cpu", "cortex-a72",
            "-accel", "tcg,thread=multi,tb-size=1024",
            "-bios", "/usr/share/qemu/qemu-uefi-aarch64.bin",
        ])
    elif os_arch == "ppc64le":
        qemu_cmd.extend(["-M", "pseries"])
    else:
        qemu_cmd.extend(["-cpu", "host"])

    append_params = (
        f"{mach.boot_params} ip=dhcp rd.neednet=1 ifname=lan0:{mach.mac} "
        f"systemd.hostname={mach.name} "
        f"root=nfs:{config.MAMA_HOST}:/machines/{mach.name}/{mach.os},rw,vers=4,nconnect=16 "
        f"console=ttyS0 console=tty"
    ).strip()

    qemu_cmd.extend([
        "-kernel", kernel,
        "-initrd", initrd,
        "-append", append_params,
    ])

    if arch == mama_arch:
        qemu_cmd.append("-enable-kvm")

    if mach.vm_params:
        qemu_cmd.extend(shlex.split(mach.vm_params))

    mach.debug(shlex.join(qemu_cmd))
    res = subprocess.run(qemu_cmd)
    if res.returncode != 0:
        mach.error("Failed to start QEMU daemon.")
        mach.set_state("boot-failed")
        mach.clear()
        return False

    time.sleep(3)

    if get_vm_pid(mach.name) is None:
        mach.error("QEMU process exited immediately after starting.")
        mach.set_state("boot-failed")
        mach.clear()
        return False

    mach.set_state("booting")

    effective_timeout = MachineRepository.get_calculated_start_timeout(
        mach.name, mach.os, "vm", default_timeout=mach.start_timeout
    )
    mach.debug(f"Using VM boot timeout: {effective_timeout}s for OS {mach.os}")

    t_start = time.time()
    ret = mach.wait_for_state("online", effective_timeout)

    if ret:
        mach.set_state("online")
        elapsed = int(time.time() - t_start)
        MachineRepository.record_start_time(mach.name, mach.os, "vm", elapsed, start_time=int(t_start))
        new_timeout = MachineRepository.get_calculated_start_timeout(mach.name, mach.os, "vm")
        mach.set("start-timeout", new_timeout)
        mach.debug(f"VM boot finished in {elapsed}s. Updated start timeout for {mach.os} to {new_timeout}s.")
    else:
        mach.set_state("boot-failed")
        kill_vm(mach.name)
        mach.clear()

    return ret