from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import os
import pathlib
import subprocess
import sys
import time
from typing import Any, List, Optional, Tuple, Union

from rich.console import Console
from rich.table import Table

try:
    import questionary
    HAS_QUESTIONARY = True
except ImportError:
    HAS_QUESTIONARY = False

try:
    from rich_argparse import RichHelpFormatter
    HELP_FORMATTER = RichHelpFormatter
    RichHelpFormatter.styles["argparse.groups"] = "bold cyan"
    RichHelpFormatter.styles["argparse.args"] = "yellow"
    RichHelpFormatter.styles["argparse.metavar"] = "green"
    RichHelpFormatter.styles["argparse.prog"] = "bold magenta"
except ImportError:
    HELP_FORMATTER = argparse.HelpFormatter

try:
    import argcomplete
    HAS_ARGCOMPLETE = True
except ImportError:
    HAS_ARGCOMPLETE = False

# Environment & helper stubs
import config
from dhcp import DnsmasqManager
from job import Job
from logger import Log, debug, error, fatal, out
from machine import Machine
from options import Options
from os_mgr import Arch, Os
from repository import MachineRepository, Util


def format_duration(seconds: int) -> str:
    """Format duration into human-readable compact units (e.g., 45s, 12m 30s, 2h 15m)."""
    if seconds < 0:
        return "0s"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if secs > 0 or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def machine_completer(prefix: str, parsed_args: Any, **kwargs: Any) -> List[str]:
    """Dynamic autocomplete callback for machine names."""
    try:
        MachineRepository.load()
        machs = Machine.get_all()
        return [m.name for m in machs if m.name.startswith(prefix)]
    except Exception:
        return []


def os_completer(prefix: str, parsed_args: Any, **kwargs: Any) -> List[str]:
    """Dynamic autocomplete callback for available OS templates."""
    try:
        path = str(pathlib.Path(config.MAMA_PATH) / "os")
        oses = Util.get_directory_contents(path, 2)
        return [o for o in oses if o.startswith(prefix)]
    except Exception:
        return []


def arch_completer(prefix: str, parsed_args: Any, **kwargs: Any) -> List[str]:
    """Dynamic autocomplete callback for buildable architectures."""
    try:
        archs = Arch.get_buildable()
        return [a for a in archs if a.startswith(prefix)]
    except Exception:
        return []


def print_usage(argv: List[str]) -> None:
    usage = """Commands:
list                                            - list configured machines
list-os [machine]                               - list available OSes
list-kernel [machine]                           - list available kernels
list-resources                                  - list all resources and what is using them
log [machine]                                   - print log file
history [machine]                               - list recorded start/stop durations and timestamps
job <job> prepare <arch> <os> [worker]          - Execute prepare part of job for arch and os
  [--args=] - Additional arguments to pass along
  [--no-ssh-timeout] - Allows ssh commands to run indefinitely

job <job> run <machine> <arch> <os>             - Execute job on specified machine
  [--args=] - Additional arguments to pass along
  [--no-ssh-timeout] - Allows ssh commands to run indefinitely
  [--no-retry] - Don't retry to start machines multiple times

build-os <arch> <os> [worker]                   - build a deployable os locally or on specified machine
  [--packages=...] - Additional packages to install

install-os <machine> <arch> <os> [name]         - install os to a machine
delete-os <machine> <arch> <os>                 - delete an os installed to a machine
copy-os <src-mach> <arch> <os> <dst-mach>       - copy an os from one machine to another
new                                             - add a new machine
delete [machine]                                - delete existing machine
info [machine]                                  - show machine detailed info
log [machine]                                   - show machine log
clear-log [machine]                             - clear the log for machine
start [machine]                                 - boot machine
start-vm [machine]                              - boot virtual instance of machine
stop [machine]                                  - triggers power button
clear [machine | all]                           - stop machine and clear stale data (ip, job, etc)
connect [machine]                               - connect to machine with ssh
set <machine>   state <state>                   - set machine boot state
                os <os>                         - set os for machine
                kernel <kernel>                 - set kernel for machine
                resources <resources>           - comma separated list of resources to set
                params <boot parameters>        - Set additional kernel command line parameters
                vmparams <VM parameters>        - Add additional QEMU command line parameters
                startcmd <command>              - Run a command instead of the normal start routine
                stopcmd <command>               - Run a command after normal halt/poweroff of machine
                powercmd <command>              - Run a command to fetch current power usage in Watts
                start-timeout <seconds>         - Set machine start timeout in seconds
                stop-timeout <seconds>          - Set machine stop timeout in seconds
get <machine> [state | status | os | params | resources | kernel
               reservation | ip | mac | vmparams |
               startcmd | stopcmd | powercmd | power |
               start-timeout | stop-timeout ]   - Get machine attribute
reserve <machine>                               - Reserve the machine for the current user
release <machine>                               - Release any reservation you have on the machine
release-forced <machine>                        - Release the machine even if you didn't reserve it
wait <machine>                                  - Wait for a machine to become available for use
serve                                           - start the iPXE boot server
dhcp sync                                       - regenerate dnsmasq config and reload service
dhcp leases                                     - list active DHCP leases
"""
    out(usage, no_eol=True, timestamp=False)


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

        if HAS_QUESTIONARY:
            try:
                choices = [
                    f"{m.name} ({m.mac}) - status: {m.get_status()}, ip: {m.get_ip() or 'N/A'}"
                    for m in machs
                ]
                ans = questionary.select("Select target machine:", choices=choices).ask()
                if not ans:
                    return False
                target_name = ans.split(" ")[0]
                m_found = Machine.get_by_name(target_name)
            except Exception:
                cmd_list()
                selected = Util.ask_from_array(machs, "Machine: ")
                if not selected:
                    return False
                m_found = selected
        else:
            cmd_list()
            selected = Util.ask_from_array(machs, "Machine: ")
            if not selected:
                return False
            m_found = selected

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

    if HAS_QUESTIONARY:
        try:
            choices = [
                f"{os_item} (Skip PXE boot)" if os_item == "NONE/NONE" else os_item
                for os_item in oses
            ]
            ans = questionary.select(f"Select OS for {mach.name}:", choices=choices).ask()
            if ans:
                clean_os = ans.split(" ")[0]
                parts = clean_os.split("/", 1)
                os_obj = Os()
                os_obj.arch = parts[0]
                os_obj.os = parts[1] if len(parts) > 1 else ""
                return os_obj
            return Os()
        except Exception:
            pass

    console = Console()
    table = Table(
        title=f"Select OS for Machine: {mach.name}",
        header_style="bold cyan",
        border_style="bright_blue",
        caption='[dim]* "NONE" will let the machine use next boot option after PXE[/dim]',
    )
    table.add_column("No.", justify="right", style="dim")
    table.add_column("Arch", style="bold yellow")
    table.add_column("OS Name", style="bold white")

    for i, os_item in enumerate(oses, start=1):
        parts = os_item.split("/", 1)
        arch = parts[0]
        os_name = parts[1] if len(parts) > 1 else ""
        table.add_row(str(i), arch, os_name)

    console.print(table)

    ask = Util.ask_from_array(oses, "OS:")
    os_obj = Os()
    if ask:
        parts = str(ask).split("/", 1)
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

    if HAS_QUESTIONARY:
        try:
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
        except Exception:
            pass

    list_kernels(mach)
    names: List[str] = list(kernels.keys()) + ["Latest"]

    kernel = Util.ask_from_array(names, "Kernel:")
    if not kernel:
        return ""

    kernel_str = str(kernel)
    if kernel_str == "default":
        kernel_str = ""

    if kernel_str.lower() == "latest":
        kernel_str = latest

    return kernel_str


def cmd_list() -> None:
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
    table.add_column("Status")
    table.add_column("State", style="cyan")
    table.add_column("On", justify="center")
    table.add_column("Pwr", justify="right", style="yellow")
    table.add_column("VM", justify="center")
    table.add_column("Job", style="italic")

    machs = Machine.get_all()

    def fetch_mach_info(mach: Machine) -> dict:
        ip = mach.get_ip() or "-"
        status = mach.get_status()

        if status == "online":
            status_str = "[bold green]online[/bold green]"
        elif status == "unreachable":
            status_str = "[bold yellow]unreachable[/bold yellow]"
        else:
            status_str = "[dim red]offline[/dim red]"

        started_str = "[green]Yes[/green]" if mach.is_started() else "[dim]-[/dim]"
        vm_str = "[cyan]Yes[/cyan]" if mach.is_vm() else "[dim]-[/dim]"

        job_str = mach.job or "-"
        if mach.job and mach.is_job_stale():
            job_str = f"{mach.job} [bold red][STALE][/bold red]"

        return {
            "name": mach.name,
            "ip": ip,
            "mac": mach.mac,
            "status_str": status_str,
            "state_str": mach.state or "-",
            "started_str": started_str,
            "power": mach.get_power(),
            "vm_str": vm_str,
            "job_str": job_str,
        }

    max_workers = min(32, len(machs)) if machs else 1
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(fetch_mach_info, machs))

    for i, data in enumerate(results, start=1):
        table.add_row(
            str(i),
            data["name"],
            data["ip"],
            data["mac"],
            data["status_str"],
            data["state_str"],
            data["started_str"],
            data["power"],
            data["vm_str"],
            data["job_str"],
        )

    console.print(table)


def cmd_new() -> None:
    name = ""
    while not name:
        if HAS_QUESTIONARY:
            try:
                name = questionary.text("Name:").ask()
            except Exception:
                name = Util.get_line("Name: ").strip()
        else:
            name = Util.get_line("Name: ").strip()

        if not name:
            return

        machs = Machine.get_all()
        for m in machs:
            if m.name == name:
                error("Name already exists")
                name = ""
                break

    m = Machine()

    mac = ""
    while not Util.is_valid_mac(mac):
        if HAS_QUESTIONARY:
            try:
                mac = questionary.text("MAC:").ask()
            except Exception:
                mac = Util.get_line("MAC: ").strip()
        else:
            mac = Util.get_line("MAC: ").strip()

        if not mac:
            return

    archs = Arch.get_buildable()

    if HAS_QUESTIONARY:
        try:
            arch = questionary.select("Select Architecture:", choices=archs).ask()
        except Exception:
            arch = Util.ask_from_array(archs, "Arch")
    else:
        console = Console()
        table = Table(
            title="Select Architecture",
            header_style="bold cyan",
            border_style="bright_blue",
        )
        table.add_column("No.", justify="right", style="dim")
        table.add_column("Architecture", style="bold white")

        for idx, a in enumerate(archs, start=1):
            table.add_row(str(idx), a)

        console.print(table)
        arch = Util.ask_from_array(archs, "Arch")

    out(f"Arch selected: {arch}\n")

    if HAS_QUESTIONARY:
        try:
            only_vm_bool = questionary.confirm("Is this only a virtual machine?").ask()
            only_vm = 1 if only_vm_bool else 0
        except Exception:
            only_vm_ans = Util.ask_yesno("Is this only a virtual machine?")
            only_vm = 1 if only_vm_ans == "y" else 0
    else:
        only_vm_ans = Util.ask_yesno("Is this only a virtual machine?")
        only_vm = 1 if only_vm_ans == "y" else 0

    m.name = name
    m.mac = mac
    m.arch = str(arch) if arch else ""
    m.os = ""
    m.kernel = ""
    m.only_vm = only_vm

    if not Util.is_root():
        out("You must be root to run this command")

    path = pathlib.Path(config.MAMA_PATH) / "machines" / name
    subprocess.run(f"sudo mkdir -p {path}", shell=True)
    subprocess.run(f"sudo touch {path / 'lock'}", shell=True)
    subprocess.run(f"sudo chmod 666 {path / 'lock'}", shell=True)

    MachineRepository.add_machine(m)
    out("New machine created")

    if getattr(config, "DHCP_ENABLED", True):
        DnsmasqManager.reload_or_restart()


def cmd_list_os(m: Union[str, bool] = False) -> None:
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
            if mach.is_started():
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


def cmd_job_prepare(job_name: str, arch: str, os_name: str, worker: str = "jobs") -> None:
    job = Job(job_name)
    job.type = "prepare"
    job.arch = arch
    job.os = os_name
    job.mach = worker
    job.execute_prepare_job()


def cmd_job_run(job_name: str, machine: str, arch: str, os_name: str) -> None:
    job = Job(job_name)
    job.type = "run"
    job.mach = machine
    job.arch = arch
    job.os = os_name
    job.execute_run_job()


def cmd_build_os(arch: str, os_name: str, builder: str = "os-builder") -> None:
    packages = Options.options.get("packages", "")

    builder_mach = select_machine(builder)
    if not isinstance(builder_mach, Machine):
        fatal("Invalid builder machine name")

    if not Arch.is_buildable(arch):
        fatal(f"Invalid architecture: {arch}")

    if not Os.is_buildable(arch, os_name):
        fatal(f"Invalid os: {arch}/{os_name}")

    mama_arch = subprocess.check_output("uname -p", shell=True, text=True).strip()
    need_sudo = "" if Util.is_root() else "sudo"

    if mama_arch == arch and builder_mach.is_only_vm():
        cmd_run_os_build_script(arch, os_name)
    else:
        subprocess.run(
            f'{need_sudo} mama job os-builder run {builder} {arch} {os_name} --args="{packages}"',
            shell=True,
        )


def cmd_run_os_build_script(arch: str, os_name: str) -> None:
    packages_str = ""
    packages = Options.options.get("packages", "").split()
    for package in packages:
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
            fatal(f"Failed to remove old version of os: {target}")

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


def cmd_install_os(machine: str, arch: str, os_name: str, name: Optional[str] = None) -> None:
    if not Util.is_root():
        out("You must be root to run this command")

    if not name:
        name = os_name

    mach = select_machine(machine)
    if not isinstance(mach, Machine):
        fatal("Invalid machine name")

    if not Arch.is_installable(arch, mach):
        fatal("Invalid architechture")

    if not Os.is_installable(arch, os_name, mach):
        fatal("Invalid os")

    src = pathlib.Path(config.MAMA_PATH) / "os" / arch / os_name
    dst = pathlib.Path(config.MAMA_PATH) / "machines" / machine / arch / name
    need_sudo = "" if Util.is_root() else "sudo"

    with Os.lock(arch, os_name, shared=True), mach.lock():
        res = subprocess.run(f"{need_sudo} rm -Rf {dst}", shell=True)
        if res.returncode != 0:
            fatal("Failed to remove previous installation of the OS")

        res = subprocess.run(f"{need_sudo} mkdir -p {dst.parent}", shell=True)
        if res.returncode != 0:
            fatal(f"Failed to create directory {dst}")

        console = Console()
        with console.status(f"[bold cyan]Installing OS template {arch}/{os_name} to machine {machine}...[/bold cyan]"):
            res = subprocess.run(f"{need_sudo} cp -r --reflink=auto {src} {dst}", shell=True)

        if res.returncode != 0:
            fatal("Failed to copy new OS to destination")

    out("OS install succeeded!")


def cmd_delete_os(machine: str, arch: str, os_name: str) -> None:
    need_sudo = "" if Util.is_root() else "sudo"
    if not Util.is_root():
        out("You must be root to run this command")

    mach = select_machine(machine)
    if not isinstance(mach, Machine):
        fatal("Invalid machine name")

    out(f"Deleting OS {machine}/{arch}/{os_name}")

    if not Os.is_runnable(arch, os_name, mach):
        fatal("OS isn't installed and cannot be deleted")

    target = pathlib.Path(config.MAMA_PATH) / "machines" / machine / arch / os_name
    with mach.lock():
        res = subprocess.run(f"{need_sudo} rm -Rf {target}", shell=True)
        if res.returncode != 0:
            fatal(f"Failed to delete OS {machine}/{arch}/{os_name}")


def cmd_copy_os(src_machine_name: str, arch: str, os_name: str, dst_machine_name: str) -> None:
    no_overwrite = getattr(Options, "options", {}).get("no-overwrite", False)

    if not Util.is_root():
        error("You must be root to run this command")

    need_sudo = "" if Util.is_root() else "sudo"

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

    with first_mach.lock(), second_mach.lock():
        out(f"Copying os {arch}/{os_name} from {src_mach.name} to {dst_mach.name}")
        src_dir = pathlib.Path(config.MAMA_PATH) / "machines" / src_mach.name / arch / os_name
        dst_arch_dir = pathlib.Path(config.MAMA_PATH) / "machines" / dst_mach.name / arch / ""

        subprocess.run(f"{need_sudo} rm -Rf {dst_os_dir}", shell=True)
        subprocess.run(f"{need_sudo} mkdir -p {dst_os_dir}", shell=True)

        console = Console()
        with console.status(f"[bold cyan]Copying OS {arch}/{os_name} from {src_mach.name} to {dst_mach.name}...[/bold cyan]"):
            subprocess.run(f"{need_sudo} cp -r --reflink=auto {src_dir} {dst_arch_dir}", shell=True)


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
            subprocess.run(f"rm -Rf {path}", shell=True)
        else:
            error(f"You're not root! You must manually delete installed OSes at {path}")

        if getattr(config, "DHCP_ENABLED", True):
            DnsmasqManager.reload_or_restart()


def cmd_info(arg: Union[str, bool]) -> None:
    mach = select_machine(arg)
    Log.set_file(False)
    if isinstance(mach, Machine):
        mach.print_info()


def cmd_log(arg: Union[str, bool]) -> None:
    arg_name = str(arg) if isinstance(arg, str) else "mama-log"
    log_file = pathlib.Path(config.MAMA_PATH) / "log" / arg_name
    if log_file.exists():
        subprocess.run(f"cat {log_file}", shell=True)
    else:
        error(f"No log for {arg_name} exists")


def cmd_tail(arg: Union[str, bool]) -> None:
    arg_name = str(arg) if isinstance(arg, str) else "mama-log"
    log_file = pathlib.Path(config.MAMA_PATH) / "log" / arg_name
    if log_file.exists():
        subprocess.run(f"tail -f {log_file}", shell=True)
    else:
        error(f"No log for {arg_name} exists")


def cmd_clear_log(arg: Union[str, bool]) -> None:
    arg_name = str(arg) if isinstance(arg, str) else "mama-log"
    log_file = pathlib.Path(config.MAMA_PATH) / "log" / arg_name
    if log_file.exists():
        subprocess.run(f"rm {log_file}", shell=True)
    else:
        error(f"No log for {arg_name} exists")


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
        for m in Machine.get_all():
            cmd_clear(m.name)
        return

    mach = select_machine(arg)
    if isinstance(mach, Machine):
        mach.stop(force=True)
        mach.clear_job()


def cmd_connect(arg: Union[str, bool]) -> None:
    mach = select_machine(arg)
    if isinstance(mach, Machine):
        ip = mach.get_ip()
        subprocess.run(
            f'ssh -o "UserKnownHostsFile=/dev/null" -o "StrictHostKeyChecking=no" root@{ip}',
            shell=True,
        )


def cmd_reserve(arg: Union[str, bool]) -> None:
    mach = select_machine(arg)
    if isinstance(mach, Machine):
        if mach.reservation:
            fatal(f"Machine is already reserved by: {mach.reservation}")

        user = subprocess.check_output("whoami", shell=True, text=True).strip()
        mach.reserve(user)


def cmd_release(arg: Union[str, bool]) -> None:
    mach = select_machine(arg)
    if isinstance(mach, Machine):
        if not mach.reservation:
            fatal("Machine is not reserved")

        user = subprocess.check_output("whoami", shell=True, text=True).strip()
        if mach.reservation != user:
            fatal(
                "Failed to release machine. You do not hold the reservation. "
                "Use the command release-forced to force release the machine."
            )

        mach.reserve("")


def cmd_release_forced(arg: Union[str, bool]) -> None:
    mach = select_machine(arg)
    if isinstance(mach, Machine):
        mach.reserve("")


def cmd_wait(arg: Union[str, bool]) -> None:
    mach = select_machine(arg)
    if isinstance(mach, Machine):
        mach.wait()


def cmd_serve() -> None:
    import boot
    boot.main()


def bootinfo(mac: str, server_ip: str, port: Union[int, str, None] = None) -> Tuple[str, str, str]:
    mach = Machine.find_by_mac(mac)
    if not mach:
        new_mach = Machine(name=mac, mac=mac)
        new_mach.only_vm = 0
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
            "start-timeout", "stop-timeout"
        ]
        if HAS_QUESTIONARY:
            try:
                attr = questionary.select("Select attribute to set:", choices=attrs).ask()
            except Exception:
                attr = Util.get_line(
                    "{state | os | kernel | resources | params | vmparams | startcmd | stopcmd | powercmd | start-timeout | stop-timeout}: "
                ).strip()
        else:
            attr = Util.get_line(
                "{state | os | kernel | resources | params | vmparams | startcmd | stopcmd | powercmd | start-timeout | stop-timeout}: "
            ).strip()

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
    mach = select_machine(machine)

    if not isinstance(mach, Machine):
        return

    if not attr:
        attrs = [
            "state", "status", "os", "params", "resources", "kernel", "reservation",
            "ip", "mac", "vmparams", "startcmd", "stopcmd", "powercmd", "power",
            "start-timeout", "stop-timeout"
        ]
        if HAS_QUESTIONARY:
            try:
                attr = questionary.select("Select attribute to get:", choices=attrs).ask()
            except Exception:
                attr = Util.get_line(
                    "{state | status | os | params | resources | kernel | reservation | ip | mac | vmparams | startcmd | stopcmd | powercmd | power | start-timeout | stop-timeout}: "
                ).strip()
        else:
            attr = Util.get_line(
                "{state | status | os | params | resources | kernel | reservation | ip | mac | vmparams | startcmd | stopcmd | powercmd | power | start-timeout | stop-timeout}: "
            ).strip()

    out(str(mach.get(attr)))


def setup_cli_parser() -> argparse.ArgumentParser:
    parent_parser = argparse.ArgumentParser(add_help=False, formatter_class=HELP_FORMATTER)
    parent_parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parent_parser.add_argument("--debug-lock", dest="debug_lock", action="store_true", help="Enable lock debugging")
    parent_parser.add_argument("--debug-pid", dest="debug_pid", action="store_true", help="Enable PID debugging")
    parent_parser.add_argument("--args", type=str, default="", help="Additional arguments to pass along")
    parent_parser.add_argument("--packages", type=str, default="", help="Additional packages to install")
    parent_parser.add_argument("--no-ssh-timeout", dest="no_ssh_timeout", action="store_true", help="Allows SSH commands to run indefinitely")
    parent_parser.add_argument("--no-retry", dest="no_retry", action="store_true", help="Don't retry starting machines multiple times")
    parent_parser.add_argument("--no-overwrite", dest="no_overwrite", action="store_true", help="Skip overwriting existing OS on copy")

    root_parser = argparse.ArgumentParser(
        prog="mama",
        description="MAMA Machine and OS Management Framework",
        parents=[parent_parser],
        formatter_class=HELP_FORMATTER,
    )
    subparsers = root_parser.add_subparsers(dest="command", help="Available commands", metavar="<command>")

    subparsers.add_parser("list", parents=[parent_parser], help="List configured machines", formatter_class=HELP_FORMATTER)

    p_list_os = subparsers.add_parser("list-os", parents=[parent_parser], help="List available OSes", formatter_class=HELP_FORMATTER)
    p_list_os_mach = p_list_os.add_argument("machine", nargs="?", default=False, help="Target machine")
    p_list_os_mach.completer = machine_completer

    p_list_kernel = subparsers.add_parser("list-kernel", parents=[parent_parser], help="List available kernels", formatter_class=HELP_FORMATTER)
    p_list_kernel_mach = p_list_kernel.add_argument("machine", help="Target machine")
    p_list_kernel_mach.completer = machine_completer

    subparsers.add_parser("list-resources", parents=[parent_parser], help="List all resources", formatter_class=HELP_FORMATTER)

    p_log = subparsers.add_parser("log", parents=[parent_parser], help="Print log file", formatter_class=HELP_FORMATTER)
    p_log_mach = p_log.add_argument("machine", nargs="?", default=False, help="Target machine")
    p_log_mach.completer = machine_completer

    p_tail = subparsers.add_parser("tail", parents=[parent_parser], help="Tail log file", formatter_class=HELP_FORMATTER)
    p_tail_mach = p_tail.add_argument("machine", nargs="?", default=False, help="Target machine")
    p_tail_mach.completer = machine_completer

    p_clear_log = subparsers.add_parser("clear-log", parents=[parent_parser], help="Clear log for machine", formatter_class=HELP_FORMATTER)
    p_clear_log_mach = p_clear_log.add_argument("machine", nargs="?", default=False, help="Target machine")
    p_clear_log_mach.completer = machine_completer

    p_hist = subparsers.add_parser("history", parents=[parent_parser], help="Show start/stop timing history and logs", formatter_class=HELP_FORMATTER)
    p_hist_mach = p_hist.add_argument("machine", nargs="?", default=False, help="Target machine (optional)")
    p_hist_mach.completer = machine_completer

    # Job subcommands
    job_p = subparsers.add_parser("job", parents=[parent_parser], help="Execute job workflows", formatter_class=HELP_FORMATTER)
    job_p.add_argument("job_name", help="Job name")
    job_sub = job_p.add_subparsers(dest="job_action", help="Job action", metavar="<action>")

    prep_p = job_sub.add_parser("prepare", parents=[parent_parser], help="Execute prepare job", formatter_class=HELP_FORMATTER)
    prep_p_arch = prep_p.add_argument("arch", help="Target architecture")
    prep_p_arch.completer = arch_completer
    prep_p_os = prep_p.add_argument("os", help="Target OS")
    prep_p_os.completer = os_completer
    prep_p_worker = prep_p.add_argument("worker", nargs="?", default="jobs", help="Worker machine name")
    prep_p_worker.completer = machine_completer

    run_p = job_sub.add_parser("run", parents=[parent_parser], help="Execute run job", formatter_class=HELP_FORMATTER)
    run_p_mach = run_p.add_argument("machine", help="Target machine")
    run_p_mach.completer = machine_completer
    run_p_arch = run_p.add_argument("arch", help="Target architecture")
    run_p_arch.completer = arch_completer
    run_p_os = run_p.add_argument("os", help="Target OS")
    run_p_os.completer = os_completer

    # OS Build and Management
    p_build = subparsers.add_parser("build-os", parents=[parent_parser], help="Build a deployable OS", formatter_class=HELP_FORMATTER)
    p_build_arch = p_build.add_argument("arch", help="Architecture")
    p_build_arch.completer = arch_completer
    p_build_os = p_build.add_argument("os", help="OS name")
    p_build_os.completer = os_completer
    p_build_builder = p_build.add_argument("builder", nargs="?", default="os-builder", help="Builder machine name")
    p_build_builder.completer = machine_completer

    p_run_script = subparsers.add_parser("run-os-build-script", parents=[parent_parser], help="Run local OS build script", formatter_class=HELP_FORMATTER)
    p_run_script_arch = p_run_script.add_argument("arch", help="Architecture")
    p_run_script_arch.completer = arch_completer
    p_run_script_os = p_run_script.add_argument("os", help="OS name")
    p_run_script_os.completer = os_completer

    p_install = subparsers.add_parser("install-os", parents=[parent_parser], help="Install OS to a machine", formatter_class=HELP_FORMATTER)
    p_install_mach = p_install.add_argument("machine", help="Target machine")
    p_install_mach.completer = machine_completer
    p_install_arch = p_install.add_argument("arch", help="Architecture")
    p_install_arch.completer = arch_completer
    p_install_os = p_install.add_argument("os", help="Source OS name")
    p_install_os.completer = os_completer
    p_install.add_argument("name", nargs="?", default=None, help="Destination installation name")

    p_del_os = subparsers.add_parser("delete-os", parents=[parent_parser], help="Delete an OS installed on a machine", formatter_class=HELP_FORMATTER)
    p_del_os_mach = p_del_os.add_argument("machine", help="Target machine")
    p_del_os_mach.completer = machine_completer
    p_del_os_arch = p_del_os.add_argument("arch", help="Architecture")
    p_del_os_arch.completer = arch_completer
    p_del_os.add_argument("os", help="OS name")

    p_copy_os = subparsers.add_parser("copy-os", parents=[parent_parser], help="Copy OS from one machine to another", formatter_class=HELP_FORMATTER)
    p_copy_src = p_copy_os.add_argument("src_mach", help="Source machine")
    p_copy_src.completer = machine_completer
    p_copy_arch = p_copy_os.add_argument("arch", help="Architecture")
    p_copy_arch.completer = arch_completer
    p_copy_os_name = p_copy_os.add_argument("os", help="OS name")
    p_copy_os_name.completer = os_completer
    p_copy_dst = p_copy_os.add_argument("dst_mach", help="Destination machine")
    p_copy_dst.completer = machine_completer

    # Machine operations
    subparsers.add_parser("new", parents=[parent_parser], help="Add a new machine", formatter_class=HELP_FORMATTER)

    p_del = subparsers.add_parser("delete", parents=[parent_parser], help="Delete an existing machine", formatter_class=HELP_FORMATTER)
    p_del_mach = p_del.add_argument("machine", nargs="?", default=False, help="Machine to delete")
    p_del_mach.completer = machine_completer

    p_info = subparsers.add_parser("info", parents=[parent_parser], help="Show detailed machine info", formatter_class=HELP_FORMATTER)
    p_info_mach = p_info.add_argument("machine", nargs="?", default=False, help="Target machine")
    p_info_mach.completer = machine_completer

    p_start = subparsers.add_parser("start", parents=[parent_parser], help="Boot machine", formatter_class=HELP_FORMATTER)
    p_start_mach = p_start.add_argument("machine", nargs="?", default=False, help="Target machine")
    p_start_mach.completer = machine_completer

    p_vm = subparsers.add_parser("start-vm", parents=[parent_parser], help="Boot virtual instance of machine", formatter_class=HELP_FORMATTER)
    p_vm_mach = p_vm.add_argument("machine", nargs="?", default=False, help="Target machine")
    p_vm_mach.completer = machine_completer

    p_stop = subparsers.add_parser("stop", parents=[parent_parser], help="Stop machine", formatter_class=HELP_FORMATTER)
    p_stop_mach = p_stop.add_argument("machine", nargs="?", default=False, help="Target machine")
    p_stop_mach.completer = machine_completer

    p_clear = subparsers.add_parser("clear", parents=[parent_parser], help="Stop machine and clear state", formatter_class=HELP_FORMATTER)
    p_clear_mach = p_clear.add_argument("machine", nargs="?", default=False, help="Target machine or 'all'")
    p_clear_mach.completer = machine_completer

    p_conn = subparsers.add_parser("connect", parents=[parent_parser], help="SSH connect to machine", formatter_class=HELP_FORMATTER)
    p_conn_mach = p_conn.add_argument("machine", nargs="?", default=False, help="Target machine")
    p_conn_mach.completer = machine_completer

    p_set = subparsers.add_parser("set", parents=[parent_parser], help="Set machine attributes", formatter_class=HELP_FORMATTER)
    p_set_mach = p_set.add_argument("machine", nargs="?", default=False, help="Target machine")
    p_set_mach.completer = machine_completer
    p_set.add_argument("attr", nargs="?", default=None, help="Attribute name")
    p_set.add_argument("val", nargs="*", default=[], help="Attribute values")

    p_get = subparsers.add_parser("get", parents=[parent_parser], help="Get machine attribute", formatter_class=HELP_FORMATTER)
    p_get_mach = p_get.add_argument("machine", nargs="?", default=False, help="Target machine")
    p_get_mach.completer = machine_completer
    p_get.add_argument("attr", nargs="?", default=None, help="Attribute name")

    p_res = subparsers.add_parser("reserve", parents=[parent_parser], help="Reserve machine", formatter_class=HELP_FORMATTER)
    p_res_mach = p_res.add_argument("machine", nargs="?", default=False, help="Target machine")
    p_res_mach.completer = machine_completer

    p_rel = subparsers.add_parser("release", parents=[parent_parser], help="Release machine reservation", formatter_class=HELP_FORMATTER)
    p_rel_mach = p_rel.add_argument("machine", nargs="?", default=False, help="Target machine")
    p_rel_mach.completer = machine_completer

    p_rel_f = subparsers.add_parser("release-forced", parents=[parent_parser], help="Force release machine reservation", formatter_class=HELP_FORMATTER)
    p_rel_f_mach = p_rel_f.add_argument("machine", nargs="?", default=False, help="Target machine")
    p_rel_f_mach.completer = machine_completer

    p_wait = subparsers.add_parser("wait", parents=[parent_parser], help="Wait for machine availability", formatter_class=HELP_FORMATTER)
    p_wait_mach = p_wait.add_argument("machine", nargs="?", default=False, help="Target machine")
    p_wait_mach.completer = machine_completer

    subparsers.add_parser("serve", parents=[parent_parser], help="Start iPXE boot server", formatter_class=HELP_FORMATTER)

    p_ipxe = subparsers.add_parser("ipxe", parents=[parent_parser], help="Generate iPXE boot script", formatter_class=HELP_FORMATTER)
    p_ipxe.add_argument("mac", help="Machine MAC address")
    p_ipxe.add_argument("server_ip", help="Server IP address")
    p_ipxe.add_argument("port", nargs="?", default=None, help="Server port (optional)")

    p_bootinfo = subparsers.add_parser("bootinfo", parents=[parent_parser], help="Output kernel, initrd, and append params", formatter_class=HELP_FORMATTER)
    p_bootinfo.add_argument("mac", help="Machine MAC address")
    p_bootinfo.add_argument("server_ip", help="Server IP address")
    p_bootinfo.add_argument("port", nargs="?", default=None, help="Server port (optional)")

    # DHCP subcommands
    p_dhcp = subparsers.add_parser("dhcp", parents=[parent_parser], help="Manage dnsmasq DHCP server", formatter_class=HELP_FORMATTER)
    dhcp_sub = p_dhcp.add_subparsers(dest="dhcp_action", help="DHCP action", metavar="<action>")
    dhcp_sub.add_parser("sync", parents=[parent_parser], help="Regenerate config and reload dnsmasq", formatter_class=HELP_FORMATTER)
    dhcp_sub.add_parser("leases", parents=[parent_parser], help="Show active DHCP leases", formatter_class=HELP_FORMATTER)

    return root_parser


def main() -> None:
    parser = setup_cli_parser()

    if HAS_ARGCOMPLETE:
        argcomplete.autocomplete(parser)

    if len(sys.argv) <= 1:
        parser.print_help()
        sys.exit(0)

    parsed_args = parser.parse_args()

    config.DEBUG = parsed_args.debug
    config.DEBUG_LOCK = parsed_args.debug_lock
    config.DEBUG_PID = parsed_args.debug_pid

    Options.options = {
        "debug": parsed_args.debug,
        "debug-lock": parsed_args.debug_lock,
        "debug-pid": parsed_args.debug_pid,
        "args": parsed_args.args or "",
        "packages": parsed_args.packages or "",
        "no-ssh-timeout": parsed_args.no_ssh_timeout,
        "no-retry": parsed_args.no_retry,
        "no-overwrite": parsed_args.no_overwrite,
    }

    db_path = getattr(config, "DB_FILE", f"{getattr(config, 'MAMA_PATH', '/usr/share/mama')}/mama.db")
    if MachineRepository.load() is False:
        fatal(f"Failed to initialize database: {db_path}\n")

    cmd = parsed_args.command

    if cmd == "list":
        cmd_list()
    elif cmd == "list-os":
        cmd_list_os(parsed_args.machine)
    elif cmd == "list-kernel":
        cmd_list_kernel(parsed_args.machine)
    elif cmd == "list-resources":
        cmd_list_resources()
    elif cmd == "history":
        cmd_history(parsed_args.machine)
    elif cmd == "job":
        if parsed_args.job_action == "prepare":
            cmd_job_prepare(parsed_args.job_name, parsed_args.arch, parsed_args.os, parsed_args.worker)
        elif parsed_args.job_action == "run":
            cmd_job_run(parsed_args.job_name, parsed_args.machine, parsed_args.arch, parsed_args.os)
        else:
            fatal("Invalid job command")
    elif cmd == "build-os":
        cmd_build_os(parsed_args.arch, parsed_args.os, parsed_args.builder)
    elif cmd == "run-os-build-script":
        cmd_run_os_build_script(parsed_args.arch, parsed_args.os)
    elif cmd == "install-os":
        cmd_install_os(parsed_args.machine, parsed_args.arch, parsed_args.os, parsed_args.name)
    elif cmd == "delete-os":
        cmd_delete_os(parsed_args.machine, parsed_args.arch, parsed_args.os)
    elif cmd == "copy-os":
        cmd_copy_os(parsed_args.src_mach, parsed_args.arch, parsed_args.os, parsed_args.dst_mach)
    elif cmd == "new":
        cmd_new()
    elif cmd == "delete":
        cmd_delete(parsed_args.machine)
    elif cmd == "info":
        cmd_info(parsed_args.machine)
    elif cmd == "log":
        cmd_log(parsed_args.machine)
    elif cmd == "tail":
        cmd_tail(parsed_args.machine)
    elif cmd == "clear-log":
        cmd_clear_log(parsed_args.machine)
    elif cmd == "start":
        cmd_start(parsed_args.machine)
    elif cmd == "start-vm":
        cmd_start_vm(parsed_args.machine)
    elif cmd == "stop":
        cmd_stop(parsed_args.machine)
    elif cmd == "clear":
        cmd_clear(parsed_args.machine)
    elif cmd == "connect":
        cmd_connect(parsed_args.machine)
    elif cmd == "set":
        cmd_set(parsed_args.machine, parsed_args.attr, parsed_args.val)
    elif cmd == "get":
        cmd_get(parsed_args.machine, parsed_args.attr)
    elif cmd == "reserve":
        cmd_reserve(parsed_args.machine)
    elif cmd == "release":
        cmd_release(parsed_args.machine)
    elif cmd == "release-forced":
        cmd_release_forced(parsed_args.machine)
    elif cmd == "wait":
        cmd_wait(parsed_args.machine)
    elif cmd == "serve":
        cmd_serve()
    elif cmd == "ipxe":
        cmd_ipxe(parsed_args.mac, parsed_args.server_ip, parsed_args.port)
    elif cmd == "bootinfo":
        cmd_bootinfo(parsed_args.mac, parsed_args.server_ip, parsed_args.port)
    elif cmd == "dhcp":
        if parsed_args.dhcp_action == "sync":
            if DnsmasqManager.reload_or_restart():
                out("dnsmasq synchronized and reloaded successfully.")
            else:
                fatal("Failed to sync dnsmasq.")
        elif parsed_args.dhcp_action == "leases":
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
            parser.parse_args(["dhcp", "--help"])
    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()