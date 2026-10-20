from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
import pathlib
import subprocess
import sys
import time
from typing import Any, List, Optional, Union

import questionary
from rich.console import Console
from rich.table import Table

from . import config
from .bootinfo import bootinfo
from .builder import build_os, run_os_build_script
from .cli_parser import format_duration
from .dhcp import DnsmasqManager
from .job import Job
from .job_queue import JobQueue
from .logger import Log, debug, error, fatal, out
from .machine import Machine
from .nfs import NfsManager
from .os_mgr import Arch, Os
from .repository import MachineRepository
from .util import Util


def select_machine(arg: Union[str, bool, None] = False) -> Union[Machine, bool]:
    Log.set_file(False)

    machs = Machine.get_all()
    m_found: Optional[Machine] = None

    if arg:
        arg_str = str(arg)
        for mach in machs:
            if arg_str == mach.name or arg_str == mach.mac:
                m_found = mach
                break

    if not m_found and arg:
        error(f"Machine with name/mac {arg} not found")
        return False

    if not arg:
        if not machs:
            error("No machines configured")
            return False

        choices = [
            f"{m.name} ({m.mac}) - state: {m.get_state()}, ip: {m.get_ip() or 'N/A'}"
            for m in machs
        ]
        ans = questionary.select("Select target machine:", choices=choices).ask()
        if not ans:
            return False
        target_name = ans.split(" ")[0]
        m_found = Machine.get_by_name(target_name)

    mach = Machine()
    if m_found:
        mach.fill_from_dict(m_found.to_dict())
        Log.set_file(mach.name)

    return mach


def select_os(mach: Machine) -> Os:
    path = os.path.join(config.MAMA_PATH, "machines", mach.name, "")
    oses = Util.get_directory_contents(path, 2)
    if "NONE/NONE" not in oses:
        oses.insert(0, "NONE/NONE")

    choices = [
        f"{os_item} (Skip PXE boot)" if os_item == "NONE/NONE" else os_item
        for os_item in oses
    ]
    ans = questionary.select(f"Select OS for {mach.name}:", choices=choices).ask()
    os_obj = Os()
    if ans:
        clean_os = ans.split(" ")[0]
        parts = clean_os.split("/", 1)
        os_obj.arch = parts[0]
        os_obj.os = parts[1] if len(parts) > 1 else ""
    return os_obj


def list_kernels(mach: Machine) -> None:
    kernels = mach.get_kernels()

    console = Console()
    table = Table(
        title=f"Available Kernels: {mach.name}",
        header_style="bold cyan",
        border_style="bright_blue",
    )
    table.add_column("No.", justify="right", style="dim")
    table.add_column("Kernel", style="bold white")
    table.add_column("Last Modified", style="green")

    i = 1
    for kernel, date_str in kernels.items():
        table.add_row(str(i), kernel, date_str)
        i += 1

    table.add_row(str(i), "[bold yellow]Latest[/bold yellow]", "[dim]Auto-detect newest[/dim]")
    console.print(table)


def select_kernel(mach: Machine) -> str:
    kernels = mach.get_kernels()

    latest = ""
    latest_date = ""

    for name, date_str in kernels.items():
        if date_str >= latest_date:
            latest_date = date_str
            latest = name

    choices = [
        f"{name} (Modified: {date_str})"
        for name, date_str in kernels.items()
    ]
    choices.append("Latest (Auto-detect newest)")

    ans = questionary.select(f"Select Kernel for {mach.name}:", choices=choices).ask()
    if not ans:
        return ""

    selected_kernel = ans.split(" ")[0]
    if selected_kernel.lower() == "latest":
        return latest
    return selected_kernel


def cmd_list(
    item: Union[str, bool, None] = "machines",
    target: Union[str, bool, None] = False,
    fast: bool = False,
) -> None:
    """Unified listing handler for machines, OS templates, kernels, and resources."""
    if isinstance(item, bool):
        fast = item
        item = "machines"

    item_str = str(item).lower() if item else "machines"

    if item_str == "os":
        cmd_list_os(m=target)
        return

    if item_str in ("kernels", "kernel"):
        if not target:
            mach = select_machine()
            if not isinstance(mach, Machine):
                return
            target = mach.name
        cmd_list_kernel(str(target))
        return

    if item_str in ("resources", "resource", "res"):
        cmd_list_resources()
        return

    if item_str not in ("machines", "machine"):
        error(f"Unknown list target '{item}'. Choose from: machines, os, kernels, resources.")
        return

    if getattr(config, "DHCP_ENABLED", True):
        try:
            DnsmasqManager.sync_leases_to_db()
        except Exception as e:
            debug(f"Failed to sync DHCP leases: {e}")

    console = Console()
    table = Table(
        title="Configured Machines",
        header_style="bold cyan",
        border_style="bright_blue",
        title_style="bold white",
    )
    table.add_column("No.", justify="right", style="dim")
    table.add_column("Name", style="bold white")
    table.add_column("IP", style="green")
    table.add_column("MAC", style="magenta")
    table.add_column("State")
    table.add_column("On", justify="center")
    table.add_column("Pwr", justify="right", style="yellow")
    table.add_column("VM", justify="center")
    table.add_column("Job", style="italic")

    machs = Machine.get_all()

    def fetch_mach_info(mach: Machine) -> dict:
        if fast:
            if not mach.is_started and mach.ip:
                mach.set_ip("")

            ip = mach.ip if mach.is_started else "-"
            raw_state = mach.state if mach.is_started else "offline"
            if not raw_state:
                raw_state = "booting" if mach.is_started else "offline"
            state_str = Machine.format_state(raw_state)

            started_str = "[green]Yes[/green]" if mach.is_started else "[dim]-[/dim]"
            vm_str = "[cyan]Yes[/cyan]" if mach.only_vm else "[dim]-[/dim]"

            job_str = mach.job or "-"
            if mach.job and mach.is_job_stale():
                job_str = f"{mach.job} [bold red][STALE][/bold red]"

            return {
                "name": mach.name,
                "ip": ip or "-",
                "mac": mach.mac,
                "state_str": state_str,
                "started_str": started_str,
                "power": "-",
                "vm_str": vm_str,
                "job_str": job_str,
            }

        ip = mach.get_ip() or "-"
        state = mach.get_state()
        state_str = Machine.format_state(state)

        started_str = "[green]Yes[/green]" if mach.is_started else "[dim]-[/dim]"
        vm_str = "[cyan]Yes[/cyan]" if mach.is_vm() else "[dim]-[/dim]"

        job_str = mach.job or "-"
        if mach.job and mach.is_job_stale():
            job_str = f"{mach.job} [bold red][STALE][/bold red]"

        power = mach.get_power()

        return {
            "name": mach.name,
            "ip": ip,
            "mac": mach.mac,
            "state_str": state_str,
            "started_str": started_str,
            "power": power,
            "vm_str": vm_str,
            "job_str": job_str,
        }

    if fast:
        results = [fetch_mach_info(m) for m in machs]
    else:
        max_workers = min(32, len(machs)) if machs else 1
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            results = list(executor.map(fetch_mach_info, machs))

    for i, data in enumerate(results, start=1):
        table.add_row(
            str(i),
            data["name"],
            data["ip"],
            data["mac"],
            data["state_str"],
            data["started_str"],
            data["power"],
            data["vm_str"],
            data["job_str"],
        )

    console.print(table)


def cmd_queue(arg: Union[str, bool, None] = False) -> None:
    target_mach: Optional[str] = None
    if arg:
        mach = select_machine(arg)
        if not isinstance(mach, Machine):
            return
        target_mach = mach.name

    queue_entries = JobQueue.get_queue(target_mach or "")

    console = Console()
    table = Table(
        title=f"Machine Job Queue{': ' + target_mach if target_mach else ' (All Machines)'}",
        header_style="bold cyan",
        border_style="bright_blue",
        title_style="bold white",
    )
    table.add_column("Pos", justify="right", style="dim")
    table.add_column("Machine", style="bold white")
    table.add_column("Queued Job", style="bold yellow")
    table.add_column("PID", style="cyan")
    table.add_column("Waiting Time", justify="right", style="green")
    table.add_column("Blocking Job", style="italic red")

    now = int(time.time())
    for pos, item in enumerate(queue_entries, start=1):
        mach = Machine.get_by_name(item["machine_name"])
        blocking_str = "-"
        if mach and mach.job:
            pid_str = f" (PID {mach.job_pid})" if mach.job_pid else ""
            blocking_str = f"{mach.job}{pid_str}"
        elif mach and mach.is_started:
            blocking_str = f"Machine active (state: {mach.state})"

        waiting_time = format_duration(max(0, now - item["queued_at"]))

        table.add_row(
            str(pos),
            item["machine_name"],
            item["job"],
            str(item["pid"]),
            waiting_time,
            blocking_str,
        )

    if not queue_entries:
        out(f"No jobs are currently waiting in the queue{': ' + target_mach if target_mach else ''}.")
        return

    console.print(table)


def cmd_new() -> None:
    name = ""
    while not name:
        ans = questionary.text("Name:").ask()
        if not ans:
            return
        name = ans.strip()

        machs = Machine.get_all()
        for m in machs:
            if m.name == name:
                error("Name already exists")
                name = ""
                break

    mac = ""
    while not Util.is_valid_mac(mac):
        ans = questionary.text("MAC:").ask()
        if not ans:
            return
        mac = ans.strip()
        if not Util.is_valid_mac(mac):
            error("Invalid MAC address format (e.g. 52:54:00:12:34:56)")

    archs = Arch.get_buildable()
    arch = questionary.select("Select Architecture:", choices=archs).ask()
    if not arch:
        return

    out(f"Arch selected: {arch}\n")

    only_vm = bool(questionary.confirm("Is this only a virtual machine?").ask())

    m = Machine(
        name=name,
        mac=mac,
        arch=str(arch),
        only_vm=only_vm,
    )

    path = pathlib.Path(config.MAMA_PATH) / "machines" / name
    if Util.is_root():
        path.mkdir(parents=True, exist_ok=True)
        (path / "lock").touch(mode=0o666, exist_ok=True)
        (path / "lock").chmod(0o666)
    else:
        subprocess.run(["sudo", "mkdir", "-p", str(path)])
        subprocess.run(["sudo", "touch", str(path / "lock")])
        subprocess.run(["sudo", "chmod", "666", str(path / "lock")])

    MachineRepository.add_machine(m)
    out("New machine created")

    if getattr(config, "DHCP_ENABLED", True):
        DnsmasqManager.reload_or_restart()


def cmd_list_os(m: Union[str, bool, None] = False) -> None:
    console = Console()
    if m:
        mach = select_machine(m)
        if not isinstance(mach, Machine):
            return
        title = f"Installed OSes for Machine: {mach.name}"
        path = os.path.join(config.MAMA_PATH, "machines", mach.name, "")
    else:
        title = "Available OS Templates"
        path = os.path.join(config.MAMA_PATH, "os", "")

    table = Table(
        title=title,
        header_style="bold cyan",
        border_style="bright_blue",
    )
    table.add_column("No.", justify="right", style="dim")
    table.add_column("Architecture", style="bold yellow")
    table.add_column("OS Name", style="bold white")

    oses = Util.get_directory_contents(path, 2)
    for idx, os_item in enumerate(oses, start=1):
        parts = os_item.split("/", 1)
        arch = parts[0]
        os_name = parts[1] if len(parts) > 1 else ""
        table.add_row(str(idx), arch, os_name)

    console.print(table)


def cmd_list_kernel(m: str) -> None:
    mach = select_machine(m)
    if not isinstance(mach, Machine):
        sys.exit(1)

    out(f"Listing available kernels for machine: {mach.name}")
    list_kernels(mach)


def cmd_list_resources() -> None:
    machs = Machine.get_all()
    resources: dict = {}

    for mach in machs:
        m_res = mach.resources.split()
        for res in m_res:
            if not res:
                continue
            entry = f" {mach.name}" if res in resources else mach.name
            if mach.is_started:
                entry += "*"
            resources[res] = resources.get(res, "") + entry

    console = Console()
    table = Table(
        title="Resource Assignments (* = Machine Active)",
        header_style="bold cyan",
        border_style="bright_blue",
    )
    table.add_column("Resource", style="bold yellow")
    table.add_column("Assigned Machines", style="bold white")

    for res in sorted(resources.keys()):
        mach_list = resources[res].split()
        styled_machs = []
        for m in mach_list:
            if m.endswith("*"):
                styled_machs.append(f"[bold green]{m}[/bold green]")
            else:
                styled_machs.append(f"[dim]{m}[/dim]")
        table.add_row(res, ", ".join(styled_machs))

    console.print(table)


def cmd_history(arg: Union[str, bool, None] = False) -> None:
    target_mach: Optional[str] = None
    if arg:
        mach = select_machine(arg)
        if not isinstance(mach, Machine):
            return
        target_mach = mach.name

    records = MachineRepository.get_boot_history(target_mach)
    if not records:
        out(f"No boot history recorded{' for ' + target_mach if target_mach else ''}.")
        return

    console = Console()
    table = Table(
        title=f"Machine Run History{': ' + target_mach if target_mach else ' (All Machines)'}",
        header_style="bold cyan",
        border_style="bright_blue",
        title_style="bold white",
    )
    table.add_column("No.", justify="right", style="dim")
    table.add_column("Machine", style="bold white")
    table.add_column("OS", style="bold yellow")
    table.add_column("Mode", justify="center")
    table.add_column("Start Time", justify="right", style="green")
    table.add_column("Stop Time", justify="right", style="yellow")
    table.add_column("Recorded At", style="cyan")

    now = int(time.time())

    for idx, row in enumerate(records, start=1):
        mode_str = "[cyan]VM[/cyan]" if row["boot_type"] == "vm" else "[magenta]Bare Metal[/magenta]"
        start_dur = row.get("start_duration")
        if start_dur is None:
            start_dur = row.get("duration")
        start_str = f"{start_dur}s" if start_dur is not None else "[dim]-[/dim]"

        stop_dur = row.get("stop_duration")
        stop_str = f"{stop_dur}s" if stop_dur is not None else "[dim]-[/dim]"

        created_str = row.get("created_at") or "-"
        start_ts = row.get("timestamp")
        stop_ts = row.get("stop_timestamp")

        if stop_ts and start_ts:
            duration_until_stop = max(0, stop_ts - start_ts)
            recorded_str = f"{created_str} [dim]({format_duration(duration_until_stop)})[/dim]"
        elif stop_dur is None and start_ts:
            active_duration = max(0, now - start_ts)
            recorded_str = f"{created_str} [bold green](running: {format_duration(active_duration)})[/bold green]"
        else:
            recorded_str = created_str

        table.add_row(
            str(idx),
            row["machine_name"],
            row["os"] or "-",
            mode_str,
            start_str,
            stop_str,
            recorded_str,
        )

    console.print(table)


def cmd_job_prepare(
    job_name: str,
    arch: str,
    os_name: str,
    worker: str = "jobs",
    args: str = "",
    no_ssh_timeout: bool = False,
) -> None:
    job = Job(job_name, args=args, no_ssh_timeout=no_ssh_timeout)
    job.type = "prepare"
    job.arch = arch
    job.os = os_name
    job.mach = worker
    job.execute_prepare_job()


def cmd_job_run(
    job_name: str,
    machine: str,
    arch: str,
    os_name: str,
    args: str = "",
    no_ssh_timeout: bool = False,
    no_retry: bool = False,
) -> None:
    job = Job(job_name, args=args, no_ssh_timeout=no_ssh_timeout, no_retry=no_retry)
    job.type = "run"
    job.mach = machine
    job.arch = arch
    job.os = os_name
    job.execute_run_job()


def cmd_install_os(machine: str, arch: str, os_name: str, name: Optional[str] = None) -> None:
    if not name:
        name = os_name

    mach = select_machine(machine)
    if not isinstance(mach, Machine):
        fatal("Invalid machine name")

    if not Arch.is_installable(arch, mach):
        fatal("Invalid architecture")

    if not Os.is_installable(arch, os_name, mach):
        fatal("Invalid os")

    src = pathlib.Path(config.MAMA_PATH) / "os" / arch / os_name
    dst = pathlib.Path(config.MAMA_PATH) / "machines" / machine / arch / name
    sudo_prefix = [] if Util.is_root() else ["sudo"]

    with Os.lock(arch, os_name, shared=True), mach.lock():
        res = subprocess.run(sudo_prefix + ["rm", "-Rf", str(dst)])
        if res.returncode != 0:
            fatal("Failed to remove previous installation of the OS")

        res = subprocess.run(sudo_prefix + ["mkdir", "-p", str(dst.parent)])
        if res.returncode != 0:
            fatal(f"Failed to create directory {dst}")

        console = Console()
        with console.status(f"[bold cyan]Installing OS template {arch}/{os_name} to machine {machine}...[/bold cyan]"):
            res = subprocess.run(sudo_prefix + ["cp", "-r", "--reflink=auto", str(src), str(dst)])

        if res.returncode != 0:
            fatal("Failed to copy new OS to destination")

    out("OS install succeeded!")


def cmd_delete_os(machine: str, arch: str, os_name: str) -> None:
    mach = select_machine(machine)
    if not isinstance(mach, Machine):
        fatal("Invalid machine name")

    out(f"Deleting OS {machine}/{arch}/{os_name}")

    if not Os.is_runnable(arch, os_name, mach):
        fatal("OS isn't installed and cannot be deleted")

    target = pathlib.Path(config.MAMA_PATH) / "machines" / machine / arch / os_name
    sudo_prefix = [] if Util.is_root() else ["sudo"]
    with mach.lock():
        res = subprocess.run(sudo_prefix + ["rm", "-Rf", str(target)])
        if res.returncode != 0:
            fatal(f"Failed to delete OS {machine}/{arch}/{os_name}")


def cmd_copy_os(
    src_machine_name: str,
    arch: str,
    os_name: str,
    dst_machine_name: str,
    no_overwrite: bool = False,
) -> None:
    src_mach = Machine.get_by_name(src_machine_name)
    if not src_mach:
        fatal(f"Couldn't find machine with name {src_machine_name}")

    dst_mach = Machine.get_by_name(dst_machine_name)
    if not dst_mach:
        fatal(f"Couldn't find machine with name {dst_machine_name}")

    if src_mach.name == dst_mach.name:
        debug("Source and destination machines are the same. Skipping copy.")
        return

    dst_os_dir = pathlib.Path(config.MAMA_PATH) / "machines" / dst_mach.name / arch / os_name
    if no_overwrite and dst_os_dir.is_dir():
        debug("Skipping copy-os since os exists and no-overwrite was specified")
        return

    first_mach, second_mach = sorted([src_mach, dst_mach], key=lambda m: m.name)
    sudo_prefix = [] if Util.is_root() else ["sudo"]

    with first_mach.lock(), second_mach.lock():
        out(f"Copying os {arch}/{os_name} from {src_mach.name} to {dst_mach.name}")
        src_dir = pathlib.Path(config.MAMA_PATH) / "machines" / src_mach.name / arch / os_name
        dst_arch_dir = pathlib.Path(config.MAMA_PATH) / "machines" / dst_mach.name / arch / ""

        subprocess.run(sudo_prefix + ["rm", "-Rf", str(dst_os_dir)])
        subprocess.run(sudo_prefix + ["mkdir", "-p", str(dst_os_dir)])

        console = Console()
        with console.status(f"[bold cyan]Copying OS {arch}/{os_name} from {src_mach.name} to {dst_mach.name}...[/bold cyan]"):
            subprocess.run(sudo_prefix + ["cp", "-r", "--reflink=auto", str(src_dir), str(dst_arch_dir)])


def cmd_delete(arg: Union[str, bool]) -> None:
    mach = select_machine(arg)
    if isinstance(mach, Machine):
        name = mach.name
        if not name:
            error("Machine name is NULL")
            return
        MachineRepository.delete_machine(mach)
        path = pathlib.Path(config.MAMA_PATH) / "machines" / name
        if Util.is_root():
            subprocess.run(["rm", "-Rf", str(path)])
        else:
            error(f"You're not root! You must manually delete installed OSes at {path}")

        if getattr(config, "DHCP_ENABLED", True):
            DnsmasqManager.reload_or_restart()


def cmd_info(arg: Union[str, bool]) -> None:
    mach = select_machine(arg)
    Log.set_file(False)
    if isinstance(mach, Machine):
        mach.print_info()


def cmd_log(
    arg: Union[str, bool, None] = False,
    vm: bool = False,
    follow: bool = False,
    clear: bool = False,
) -> None:
    if vm:
        mach = select_machine(arg)
        if not isinstance(mach, Machine):
            return
        log_file = mach.get_vmlog_path()
        if clear:
            if log_file.exists():
                log_file.unlink()
                out(f"Cleared VM log for {mach.name}")
            else:
                error(f"No VM log for {mach.name} exists at {log_file}")
            return

        if not log_file.exists():
            error(f"No VM log for {mach.name} exists at {log_file}")
            return

        cmd = ["tail", "-f", str(log_file)] if follow else ["cat", str(log_file)]
        subprocess.run(cmd)
    else:
        arg_name = str(arg) if isinstance(arg, str) and arg else "mama-log"
        log_dir = pathlib.Path(config.MAMA_PATH) / "log"
        log_file = log_dir / arg_name
        if clear:
            vmlog_file = log_dir / f"{arg_name}-vmlog"
            cleared = False
            if log_file.exists():
                log_file.unlink()
                cleared = True
            if vmlog_file.exists():
                vmlog_file.unlink()
                cleared = True
            if cleared:
                out(f"Cleared log for {arg_name}")
            else:
                error(f"No log for {arg_name} exists")
            return

        if not log_file.exists():
            error(f"No log for {arg_name} exists at {log_file}")
            return

        cmd = ["tail", "-f", str(log_file)] if follow else ["cat", str(log_file)]
        subprocess.run(cmd)


def cmd_start(arg: Union[str, bool]) -> None:
    mach = select_machine(arg)
    if isinstance(mach, Machine):
        kernel_str = f" and kernel {mach.kernel}" if mach.kernel else ""
        mach.out(f"Starting machine with OS {mach.os}{kernel_str}")
        mach.start()


def cmd_start_vm(arg: Union[str, bool]) -> None:
    mach = select_machine(arg)
    if isinstance(mach, Machine):
        mach.start_vm()


def cmd_stop(arg: Union[str, bool]) -> None:
    mach = select_machine(arg)
    if isinstance(mach, Machine):
        mach.stop()


def cmd_clear(arg: Union[str, bool]) -> None:
    if arg == "all":
        JobQueue.clear_machine("all")
        for m in Machine.get_all():
            cmd_clear(m.name)
        return

    mach = select_machine(arg)
    if isinstance(mach, Machine):
        JobQueue.clear_machine(mach.name)
        mach.stop(force=True)
        mach.clear_job()


def cmd_connect(arg: Union[str, bool]) -> None:
    mach = select_machine(arg)
    if isinstance(mach, Machine):
        ip = mach.get_ip()
        if not ip:
            error(f"Cannot connect: Machine '{mach.name}' has no active IP address.")
            return

        ssh_cmd = [
            "ssh",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "StrictHostKeyChecking=no",
            f"root@{ip}",
        ]
        try:
            os.execvp("ssh", ssh_cmd)
        except OSError as e:
            fatal(f"Failed to execute ssh: {e}")


def cmd_reserve(arg: Union[str, bool]) -> None:
    mach = select_machine(arg)
    if isinstance(mach, Machine):
        if mach.reservation:
            fatal(f"Machine is already reserved by: {mach.reservation}")

        user = Util.get_user()
        mach.reserve(user)


def cmd_release(arg: Union[str, bool, None] = False, force: bool = False) -> None:
    mach = select_machine(arg)
    if isinstance(mach, Machine):
        if not mach.reservation and not force:
            fatal("Machine is not reserved")

        if not force:
            user = Util.get_user()
            if mach.reservation != user:
                fatal(
                    "Failed to release machine. You do not hold the reservation. "
                    "Use `mama release <machine> --force` to force release."
                )

        mach.reserve("")
        out(f"Released reservation for machine '{mach.name}'.")


def cmd_wait(arg: Union[str, bool], reason: str = "wait") -> None:
    mach = select_machine(arg)
    if isinstance(mach, Machine):
        mach.wait(reason=reason)


def cmd_serve() -> None:
    from . import boot
    boot.main()


def cmd_ipxe(mac: str, server_ip: str, port: Optional[Union[int, str]] = None) -> None:
    out("#!ipxe", timestamp=False)
    kernel, initrd, append = bootinfo(mac, server_ip, port)

    out(f"initrd {initrd} ||", timestamp=False)
    out(f"kernel {kernel} {append} ||", timestamp=False)
    out("boot || shell", timestamp=False)


def cmd_bootinfo(mac: str, server_ip: str, port: Optional[Union[int, str]] = None) -> None:
    kernel, initrd, append = bootinfo(mac, server_ip, port)

    out(initrd, timestamp=False)
    out(kernel, timestamp=False)
    out(append, timestamp=False)


def cmd_set(machine: Union[str, bool, None] = False, attr: Optional[str] = None, val_list: Optional[List[str]] = None) -> None:
    mach = select_machine(machine)

    if not isinstance(mach, Machine):
        return

    if not attr:
        attrs = [
            "state", "os", "kernel", "resources", "params",
            "vmparams", "startcmd", "stopcmd", "powercmd",
            "start-timeout", "stop-timeout", "only-vm"
        ]
        attr = questionary.select("Select attribute to set:", choices=attrs).ask()
        if not attr:
            return

    val: Any = " ".join(val_list) if val_list else False

    if attr == "os":
        if val is False:
            val = select_os(mach)
        else:
            val = Os()
            val.arch = val_list[0] if val_list and len(val_list) > 0 else "NONE"
            val.os = val_list[1] if val_list and len(val_list) > 1 else "NONE"
    elif attr == "kernel" and val is False:
        val = select_kernel(mach)

    mach.set(attr, val)

    if attr in ("ip", "mac") and getattr(config, "DHCP_ENABLED", True):
        DnsmasqManager.reload_or_restart()


def cmd_get(machine: Union[str, bool, None] = False, attr: Optional[str] = None) -> None:
    if getattr(config, "DHCP_ENABLED", True) and (not attr or attr in ("ip", "status", "state")):
        try:
            DnsmasqManager.sync_leases_to_db()
        except Exception:
            pass

    mach = select_machine(machine)

    if not isinstance(mach, Machine):
        return

    if not attr:
        attrs = [
            "state", "status", "os", "params", "resources", "kernel", "reservation",
            "ip", "mac", "vmparams", "startcmd", "stopcmd", "powercmd", "power",
            "start-timeout", "stop-timeout", "is_started", "only_vm", "nfs-mounted"
        ]
        attr = questionary.select("Select attribute to get:", choices=attrs).ask()
        if not attr:
            return

    out(str(mach.get(attr)))


def cmd_dhcp(dhcp_action: Optional[str]) -> None:
    if dhcp_action == "sync":
        if DnsmasqManager.reload_or_restart():
            DnsmasqManager.sync_leases_to_db()
            out("dnsmasq synchronized and reloaded successfully.")
        else:
            fatal("Failed to sync dnsmasq.")
    elif dhcp_action == "sync-leases":
        count = DnsmasqManager.sync_leases_to_db()
        out(f"Synchronized DHCP leases: {count} machine(s) updated.")
    elif dhcp_action == "leases":
        DnsmasqManager.sync_leases_to_db()
        leases = DnsmasqManager.get_active_leases()
        if not leases:
            out("No active DHCP leases found.")
        else:
            console = Console()
            table = Table(
                title="Active DHCP Leases",
                header_style="bold cyan",
                border_style="bright_blue",
            )
            table.add_column("MAC Address", style="bold yellow")
            table.add_column("Assigned IP", style="bold green")
            for mac, ip in leases.items():
                table.add_row(mac, ip)
            console.print(table)
    else:
        out("Unknown or missing DHCP action. See `mama dhcp --help`.")


def cmd_nfs(nfs_action: Optional[str]) -> None:
    if nfs_action == "sync":
        if NfsManager.reload_or_restart():
            out("NFS exports synchronized and nfs-server reloaded successfully.")
        else:
            fatal("Failed to sync NFS server.")
    elif nfs_action in ("status", "exports"):
        console = Console()
        active = NfsManager.is_active()
        status_str = "[bold green]Active (running)[/bold green]" if active else "[bold red]Inactive / Stopped[/bold red]"
        service_name = getattr(config, "NFS_SERVICE_NAME", "nfs-server")
        console.print(f"NFS Service ({service_name}): {status_str}")

        exports = NfsManager.get_active_exports()
        if not exports:
            out("No active NFS exports found.")
        else:
            table = Table(
                title="Active NFS Exports",
                header_style="bold cyan",
                border_style="bright_blue",
            )
            table.add_column("Export Path", style="bold yellow")
            table.add_column("Client / Network", style="bold green")
            table.add_column("Options", style="dim")
            for item in exports:
                table.add_row(
                    item.get("path", "-"),
                    item.get("client", "-"),
                    item.get("options", "-"),
                )
            console.print(table)
    else:
        out("Unknown or missing NFS action. See `mama nfs --help`.")