from __future__ import annotations

import os
import pathlib
import platform
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
    log_dir = pathlib.Path(config.MAMA_PATH) / "log"
    return log_dir / f"{machine_name}-vmlog"


def get_vmpid_path(machine_name: str) -> pathlib.Path:
    """Return path to this machine's VM pidfile."""
    log_dir = pathlib.Path(config.MAMA_PATH) / "log"
    pid_file = log_dir / f"{machine_name}-vmpid"

    # Backward compatibility: check legacy /run/mama/<machine>.pid
    legacy_pid = pathlib.Path("/run/mama") / f"{machine_name}.pid"
    if legacy_pid.exists() and not pid_file.exists():
        return legacy_pid

    return pid_file


def is_in_kvm_group() -> bool:
    """Check if current process user is in the 'kvm' system group."""
    try:
        if os.geteuid() == 0:
            return True
        import grp
        kvm_gid = grp.getgrnam("kvm").gr_gid
        return kvm_gid in os.getgroups()
    except Exception:
        return True


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


def get_default_vm_ram_mb() -> int:
    """Calculate default RAM (in MB) for virtual machines.

    If VM_DEFAULT_RAM_MB is 'auto', detects total host physical memory and
    subtracts VM_HOST_RAM_RESERVE_MB (default 4096 MB) for host processes,
    with a minimum allocation floor of 2048 MB.
    """
    configured = getattr(config, "VM_DEFAULT_RAM_MB", "auto")
    if isinstance(configured, int) and configured > 0:
        return configured
    if isinstance(configured, str) and configured.strip().isdigit():
        return int(configured.strip())

    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        phys_pages = os.sysconf("SC_PHYS_PAGES")
        host_ram_mb = (page_size * phys_pages) // (1024 * 1024)
    except (ValueError, OSError, AttributeError):
        host_ram_mb = 8192

    reserve_mb = getattr(config, "VM_HOST_RAM_RESERVE_MB", 4096)
    return max(2048, host_ram_mb - reserve_mb)


def build_qemu_cmd(mach: Machine, pid_path: pathlib.Path, vmlog_path: pathlib.Path) -> list[str]:
    """Assemble modern QEMU command arguments for virtual machine launch."""
    num_cores = os.cpu_count() or 1
    os_arch = mach.os.split("/")[0] if "/" in mach.os else mach.os
    mama_arch = platform.machine() or subprocess.check_output(["uname", "-p"], text=True).strip()

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

    qemu_cmd: list[str] = [f"qemu-system-{arch}"]

    user_vm_params = shlex.split(mach.vm_params) if mach.vm_params else []

    # SMP / Cores configuration: use user setting if provided, else config default or host cores
    if not any(arg in ("-smp", "--smp") for arg in user_vm_params):
        configured_cpus = getattr(config, "VM_DEFAULT_CPUS", None)
        cpus = configured_cpus if configured_cpus else num_cores
        qemu_cmd.extend(["-smp", str(cpus)])

    # RAM Memory configuration: use user setting if provided, else dynamic host RAM minus reserve
    if not any(arg in ("-m", "--m") for arg in user_vm_params):
        ram_mb = get_default_vm_ram_mb()
        qemu_cmd.extend(["-m", str(ram_mb)])

    qemu_cmd.extend([
        "-daemonize",
        "-pidfile", str(pid_path),
        "-netdev", "bridge,id=net0,br=br0",
        "-device", f"virtio-net-pci,netdev=net0,mac={mach.mac}",
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

    if arch == mama_arch and pathlib.Path("/dev/kvm").exists():
        qemu_cmd.append("-enable-kvm")

    if user_vm_params:
        qemu_cmd.extend(user_vm_params)

    return qemu_cmd


def start_vm(mach: Machine) -> bool:
    """Launch virtual instance of machine using QEMU."""
    if not mach.os:
        mach.error("No OS has been configured!")
        return False

    if not mach.kernel and not mach.os.endswith("/NONE"):
        mach.error("No kernel set for machine")
        return False

    if get_vm_pid(mach.name) is not None:
        mach.error("Failed to start VM. Already running.")
        return False

    if not is_in_kvm_group():
        mach.error(
            "Current user is not a member of the 'kvm' system group!\n"
            "QEMU network bridge helper requires kvm group membership.\n"
            "Please run: sudo usermod -aG kvm $USER (then reload groups with 'newgrp kvm')."
        )
        return False

    vmlog_path = get_vmlog_path(mach.name)
    pid_path = get_vmpid_path(mach.name)

    try:
        vmlog_path.parent.mkdir(parents=True, exist_ok=True)
        vmlog_path.touch(mode=0o664, exist_ok=True)
        try:
            vmlog_path.chmod(0o664)
        except OSError:
            pass
        with open(vmlog_path, "w") as f:
            pass
    except Exception as e:
        mach.debug(f"Could not prepare vmlog file {vmlog_path}: {e}")

    try:
        pid_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        mach.debug(f"Could not prepare pidfile directory {pid_path.parent}: {e}")

    now = int(time.time())
    mach.is_started = True
    mach.started_at = now
    mach.set_state("vm-start")
    MachineRepository.update_fields(mach.name, {"_is_started": 1, "started_at": now})

    mach.out(f"Starting virtual machine with OS {mach.os} and kernel {mach.kernel}")

    qemu_cmd = build_qemu_cmd(mach, pid_path, vmlog_path)
    mach.debug(shlex.join(qemu_cmd))
    res = subprocess.run(qemu_cmd)
    if res.returncode != 0:
        mach.error("Failed to start QEMU daemon.")
        mach.set_state("failed")
        mach.clear()
        return False

    time.sleep(3)

    if get_vm_pid(mach.name) is None:
        mach.error("QEMU process exited immediately after starting.")
        mach.set_state("failed")
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
        mach.set_state("failed")
        kill_vm(mach.name)
        mach.clear()

    return ret