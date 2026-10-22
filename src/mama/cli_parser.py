from __future__ import annotations

import argparse
import pathlib
from typing import Any, List

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

from . import config
from .machine import Machine
from .os_mgr import Arch
from .repository import MachineRepository
from .util import Util


def format_duration(seconds: int, max_units: Optional[int] = None) -> str:
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
    if max_units and max_units > 0:
        parts = parts[:max_units]
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

    # Unified list parser (replaces list, list-os, list-kernel, and list-resources)
    p_list = subparsers.add_parser(
        "list",
        parents=[parent_parser],
        help="List machines, OS templates, kernels, or resources",
        formatter_class=HELP_FORMATTER,
    )
    p_list.add_argument(
        "item",
        nargs="?",
        default="machines",
        choices=["machines", "os", "kernels", "kernel", "resources"],
        help="Entity type to list: machines (default), os, kernels, resources",
    )
    p_list_target = p_list.add_argument(
        "target",
        nargs="?",
        default=False,
        help="Target machine name (optional for 'os', prompted if omitted for 'kernels')",
    )
    p_list_target.completer = machine_completer
    p_list.add_argument("--fast", action="store_true", help="Skip querying power data for faster refresh (machines only)")

    p_queue = subparsers.add_parser("queue", parents=[parent_parser], help="View queued waiting jobs", formatter_class=HELP_FORMATTER)
    p_queue_mach = p_queue.add_argument("machine", nargs="?", default=False, help="Target machine (optional)")
    p_queue_mach.completer = machine_completer

    p_log = subparsers.add_parser("log", parents=[parent_parser], help="View, follow, or clear logs", formatter_class=HELP_FORMATTER)
    p_log_mach = p_log.add_argument("machine", nargs="?", default=False, help="Target machine (defaults to mama-log)")
    p_log_mach.completer = machine_completer
    p_log.add_argument("-f", "--follow", action="store_true", help="Follow log in real time (tail -f)")
    p_log.add_argument("--vm", action="store_true", help="Inspect VM serial console log")
    p_log.add_argument("--clear", action="store_true", help="Clear the log file(s)")

    p_hist = subparsers.add_parser("history", parents=[parent_parser], help="Show start/stop timing history and logs", formatter_class=HELP_FORMATTER)
    p_hist_mach = p_hist.add_argument("machine", nargs="?", default=False, help="Target machine (optional)")
    p_hist_mach.completer = machine_completer

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
    p_rel.add_argument("-f", "--force", action="store_true", help="Force release machine even if reserved by another user")

    p_wait = subparsers.add_parser("wait", parents=[parent_parser], help="Wait for machine availability", formatter_class=HELP_FORMATTER)
    p_wait_mach = p_wait.add_argument("machine", nargs="?", default=False, help="Target machine")
    p_wait_mach.completer = machine_completer
    p_wait.add_argument("reason", nargs="?", default="wait", help="Optional description of what is waiting")

    subparsers.add_parser("serve", parents=[parent_parser], help="Start iPXE boot server", formatter_class=HELP_FORMATTER)

    p_ipxe = subparsers.add_parser("ipxe", parents=[parent_parser], help="Generate iPXE boot script", formatter_class=HELP_FORMATTER)
    p_ipxe.add_argument("mac", help="Machine MAC address")
    p_ipxe.add_argument("server_ip", help="Server IP address")
    p_ipxe.add_argument("port", nargs="?", default=None, help="Server port (optional)")

    p_bootinfo = subparsers.add_parser("bootinfo", parents=[parent_parser], help="Output kernel, initrd, and append params", formatter_class=HELP_FORMATTER)
    p_bootinfo.add_argument("mac", help="Machine MAC address")
    p_bootinfo.add_argument("server_ip", help="Server IP address")
    p_bootinfo.add_argument("port", nargs="?", default=None, help="Server port (optional)")

    p_dhcp = subparsers.add_parser("dhcp", parents=[parent_parser], help="Manage dnsmasq DHCP server", formatter_class=HELP_FORMATTER)
    dhcp_sub = p_dhcp.add_subparsers(dest="dhcp_action", help="DHCP action", metavar="<action>")
    dhcp_sub.add_parser("sync", parents=[parent_parser], help="Regenerate config and reload dnsmasq", formatter_class=HELP_FORMATTER)
    dhcp_sub.add_parser("sync-leases", parents=[parent_parser], help="Sync active DHCP leases to machine database", formatter_class=HELP_FORMATTER)
    dhcp_sub.add_parser("leases", parents=[parent_parser], help="Show active DHCP leases", formatter_class=HELP_FORMATTER)

    p_nfs = subparsers.add_parser("nfs", parents=[parent_parser], help="Manage NFS server exports", formatter_class=HELP_FORMATTER)
    nfs_sub = p_nfs.add_subparsers(dest="nfs_action", help="NFS action", metavar="<action>")
    nfs_sub.add_parser("sync", parents=[parent_parser], help="Regenerate exports config and reload nfs-server", formatter_class=HELP_FORMATTER)
    nfs_sub.add_parser("status", parents=[parent_parser], help="Show NFS server service status and active exports", formatter_class=HELP_FORMATTER)
    nfs_sub.add_parser("exports", parents=[parent_parser], help="Show active NFS exports", formatter_class=HELP_FORMATTER)

    return root_parser