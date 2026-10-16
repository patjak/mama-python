from __future__ import annotations

import contextlib
from dataclasses import asdict, dataclass, fields
import datetime
import fcntl
import os
import pathlib
import subprocess
import time
from typing import Any, Callable, ClassVar, Dict, List, Optional, Tuple, Union

import questionary
from rich.console import Console
from rich.table import Table

import config
from job_queue import JobQueue
from logger import Log, debug, error, fatal, out
import net_mgr
from os_mgr import Os
from repository import MachineRepository
from util import Util
import vm_mgr


def to_bool(val: Any) -> bool:
    """Normalize SQLite/configuration booleans into native Python bool."""
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    return str(val).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Machine:
    start_timeout_default: ClassVar[int] = 4 * 60
    stop_timeout_default: ClassVar[int] = 30
    ssh_cmd_timeout: ClassVar[int] = 1 * 60
    power_cycle_delay: ClassVar[int] = 30
    resource_wait_timeout: ClassVar[int] = 20 * 60 * 60

    BOOT_STATES: ClassVar[Tuple[str, ...]] = (
        "powering-on",
        "starting-vm",
        "power-cycling",
        "waiting-for-resources",
        "dhcp-bound",
        "ipxe-boot-config",
        "fetching-kernel",
        "fetching-initrd",
        "booting",
        "nfs-mounted",
    )

    SETTABLE_FIELDS: ClassVar[Dict[str, Tuple[str, str, str]]] = {
        "resources": ("resources", "Enter new resources (space separated):", "resources"),
        "params": ("boot parameters", "Enter new boot parameters:", "boot_params"),
        "vmparams": ("VM parameters", "Enter new VM parameters:", "vm_params"),
        "startcmd": ("start command", "Enter new start command:", "startcmd"),
        "stopcmd": ("stop command", "Enter new stop command:", "stopcmd"),
        "powercmd": ("power command", "Enter new power command:", "powercmd"),
        "start-timeout": ("start-timeout", "Enter new start-timeout (leave empty for default):", "start_timeout"),
        "start_timeout": ("start-timeout", "Enter new start-timeout (leave empty for default):", "start_timeout"),
        "stop-timeout": ("stop-timeout", "Enter new stop-timeout (leave empty for default):", "stop_timeout"),
        "stop_timeout": ("stop-timeout", "Enter new stop-timeout (leave empty for default):", "stop_timeout"),
        "only-vm": ("only-vm", "Is this only a virtual machine?", "only_vm"),
        "only_vm": ("only-vm", "Is this only a virtual machine?", "only_vm"),
    }

    name: str = ""
    mac: str = ""
    ip: str = ""
    is_started: bool = False
    state: str = ""
    arch: str = ""
    os: str = ""
    kernel: str = ""
    reservation: str = ""
    resources: str = ""
    boot_params: str = ""
    vm_params: str = ""
    only_vm: bool = False
    job: str = ""
    job_pid: str = ""
    startcmd: str = ""
    stopcmd: str = ""
    powercmd: str = ""
    start_timeout: int = 240
    stop_timeout: int = 30

    def __post_init__(self) -> None:
        self.mac = net_mgr.clean_mac(self.mac)
        self.is_started = to_bool(self.is_started)
        self.only_vm = to_bool(self.only_vm)

    @property
    def _is_started(self) -> str:
        return "1" if self.is_started else "0"

    @_is_started.setter
    def _is_started(self, val: Any) -> None:
        self.is_started = to_bool(val)

    def is_only_vm(self) -> bool:
        return self.only_vm

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Machine:
        raw_started = data.get("is_started") if "is_started" in data else data.get("_is_started", False)

        try:
            start_tout = int(data.get("start_timeout") or cls.start_timeout_default)
        except (ValueError, TypeError):
            start_tout = cls.start_timeout_default

        try:
            stop_tout = int(data.get("stop_timeout") or cls.stop_timeout_default)
        except (ValueError, TypeError):
            stop_tout = cls.stop_timeout_default

        return cls(
            name=str(data.get("name") or ""),
            mac=net_mgr.clean_mac(str(data.get("mac") or "")),
            ip=str(data.get("ip") or ""),
            is_started=to_bool(raw_started),
            state=str(data.get("state") or ""),
            arch=str(data.get("arch") or ""),
            os=str(data.get("os") or ""),
            kernel=str(data.get("kernel") or ""),
            reservation=str(data.get("reservation") or ""),
            resources=str(data.get("resources") or ""),
            boot_params=str(data.get("boot_params") or ""),
            vm_params=str(data.get("vm_params") or ""),
            only_vm=to_bool(data.get("only_vm", False)),
            job=str(data.get("job") or ""),
            job_pid=str(data.get("job_pid") or ""),
            startcmd=str(data.get("startcmd") or ""),
            stopcmd=str(data.get("stopcmd") or ""),
            powercmd=str(data.get("powercmd") or ""),
            start_timeout=start_tout,
            stop_timeout=stop_tout,
        )

    def fill_from_dict(self, data: Dict[str, Any]) -> None:
        updated = self.from_dict(data)
        for f in fields(self):
            setattr(self, f.name, getattr(updated, f.name))

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["only_vm"] = 1 if self.only_vm else 0
        d["is_started"] = 1 if self.is_started else 0
        d["_is_started"] = 1 if self.is_started else 0
        return d

    def _preprocess_cmd(self, cmd: str) -> str:
        cmd = cmd.replace("$OS_ARCH", self.get_os_arch())
        cmd = cmd.replace("$MAC", self.mac)
        cmd = cmd.replace("$PID", str(os.getpid()))
        cmd = cmd.replace("$MAMA_PATH", getattr(config, "MAMA_PATH", ""))
        cmd = cmd.replace("$MAMA_HOST", getattr(config, "MAMA_HOST", ""))
        return cmd

    def get_vmlog_path(self) -> pathlib.Path:
        return vm_mgr.get_vmlog_path(self.name)

    def get_vmpid_path(self) -> pathlib.Path:
        return vm_mgr.get_vmpid_path(self.name)

    def get_vm_pid(self) -> Optional[int]:
        return vm_mgr.get_vm_pid(self.name)

    def is_vm(self) -> bool:
        if self.only_vm:
            return True
        return self.get_vm_pid() is not None

    def kill_vm(self) -> Optional[bool]:
        return vm_mgr.kill_vm(self.name)

    def start_vm(self) -> bool:
        return vm_mgr.start_vm(self)

    @contextlib.contextmanager
    def lock(self):
        lock_file = pathlib.Path(config.MAMA_PATH) / "machines" / self.name / "lock"
        try:
            lock_file.parent.mkdir(parents=True, exist_ok=True)
            if not lock_file.exists():
                lock_file.touch(mode=0o666, exist_ok=True)
        except Exception:
            pass

        if getattr(config, "DEBUG_LOCK", False):
            self.debug(f"Acquiring machine lock (PID {os.getpid()})")

        with open(lock_file, "a+") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if getattr(config, "DEBUG_LOCK", False):
                    self.debug(f"Releasing machine lock (PID {os.getpid()})")
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass

    @classmethod
    def get_all(cls) -> List[Machine]:
        rows = MachineRepository.get_all()
        machs = [cls.from_dict(row) for row in rows]
        machs.sort(key=lambda x: x.name)
        return machs

    @classmethod
    def get_by_name(cls, name: str) -> Optional[Machine]:
        row = MachineRepository.get_by_name(name)
        return cls.from_dict(row) if row else None

    @classmethod
    def find_by_mac(cls, mac: str) -> Optional[Machine]:
        row = MachineRepository.get_by_mac(net_mgr.clean_mac(mac))
        return cls.from_dict(row) if row else None

    def set_state(self, state: str) -> None:
        self.state = state
        MachineRepository.update_fields(self.name, {"state": state})
        self.debug(f"State: {state}")

    @classmethod
    def set_state_by_name(cls, name: str, state: str) -> None:
        if name:
            MachineRepository.update_fields(name, {"state": state})

    @classmethod
    def set_state_by_mac(cls, mac: str, state: str) -> None:
        if not mac:
            return
        mach = cls.find_by_mac(mac)
        if mach:
            mach.set_state(state)
        else:
            MachineRepository.update_fields(net_mgr.clean_mac(mac), {"state": state})

    def save(self) -> None:
        MachineRepository.upsert_machine(self.to_dict())

    def load(self) -> None:
        row = None
        if self.name:
            row = MachineRepository.get_by_name(self.name)
        if not row and self.mac:
            row = MachineRepository.get_by_mac(self.mac)

        if row:
            self.fill_from_dict(row)
            return
        fatal("Failed to load machine")

    def clear(self) -> None:
        self.ip = ""
        self.is_started = False
        self.state = "offline"
        MachineRepository.update_fields(self.name, {"ip": "", "_is_started": 0, "state": "offline"})
        self.debug("cleared machine state")

    def clear_job(self) -> None:
        self.job = ""
        self.job_pid = ""
        MachineRepository.update_fields(self.name, {"job": "", "job_pid": ""})
        self.debug("cleared job")

    def get_ip_from_leases(self) -> Optional[str]:
        if not self.is_started:
            return None
        return net_mgr.get_ip_from_leases(self.mac)

    def get_ip_from_mac(self) -> Optional[str]:
        if not self.is_started:
            return None
        return net_mgr.get_ip_from_mac(self.mac)

    def get_ip(self) -> Optional[str]:
        if not self.is_started:
            if self.ip:
                self.set_ip("")
            return None

        ip = self.get_ip_from_leases()
        if not ip:
            ip = self.get_ip_from_mac()

        if not ip:
            self.load()
            ip = self.ip if self.ip else None
        else:
            if self.ip != ip:
                self.debug(f"Changing IP {self.ip} -> {ip}")
                self.set_ip(ip)
                if self.is_started and self.state in ("powering-on", "starting-vm", "power-cycling", ""):
                    self.set_state("dhcp-bound")

        return ip

    def set_ip(self, ip: str) -> None:
        self.ip = ip
        MachineRepository.update_fields(self.name, {"ip": ip})

    def get_power(self) -> str:
        if not self.powercmd or not self.powercmd.strip():
            return "-"

        cmd = self._preprocess_cmd(self.powercmd)

        try:
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
            if res.returncode == 0 and res.stdout.strip():
                val_str = res.stdout.strip().splitlines()[-1].strip()
                try:
                    val_float = float(val_str)
                    p_val = round(val_float)
                    return f"{p_val}W" if p_val > 0 else "0W"
                except ValueError:
                    return val_str
        except Exception:
            pass
        return "-"

    def is_pingable(self, ip: Optional[str] = None) -> bool:
        target_ip = ip or self.get_ip()
        return net_mgr.is_pingable(target_ip) if target_ip else False

    def get_ssh_status(self) -> str:
        ip = self.get_ip()
        return net_mgr.get_ssh_status(ip) if ip else "closed"

    def is_nfs_mounted(self) -> bool:
        ip = self.get_ip()
        return net_mgr.is_nfs_mounted(ip) if ip else False

    def is_booting(self) -> bool:
        return self.state in self.BOOT_STATES or self.state.startswith("fetching-")

    def get_state(self) -> str:
        if not self.is_started:
            if self.ip:
                self.set_ip("")
            if self.state != "offline":
                self.set_state("offline")
            return "offline"

        if self.state == "stopping":
            ip = self.get_ip()
            if not ip or not self.is_pingable(ip):
                return "offline"
            return "stopping"

        ip = self.get_ip()
        if not ip:
            if self.is_booting():
                return self.state
            if self.state != "unresponsive":
                self.set_state("unresponsive")
            return "unresponsive"

        ping_ok = self.is_pingable(ip)
        ssh_ok = (self.get_ssh_status() == "open") if ping_ok else False

        if ssh_ok:
            if self.state != "online":
                self.set_state("online")
            return "online"

        if self.is_booting():
            if self.is_nfs_mounted():
                if self.state != "nfs-mounted":
                    self.set_state("nfs-mounted")
            return self.state

        if self.state == "boot-failed":
            return "boot-failed"

        if self.state != "unresponsive":
            self.set_state("unresponsive")
        return "unresponsive"

    def get_status(self) -> str:
        return self.get_state()

    def wait_for_state(self, target_state: str, seconds: Union[int, float, str], dont_print: bool = False) -> bool:
        try:
            seconds_int = int(seconds) if seconds else 0
        except (ValueError, TypeError):
            seconds_int = 0

        start_time = time.time()

        if dont_print:
            while self.get_state() != target_state:
                curr_state = self.get_state()
                if target_state == "online" and curr_state == "boot-failed":
                    return False
                time_passed = time.time() - start_time
                time.sleep(1)
                if seconds_int > 0 and time_passed >= seconds_int:
                    return False
            return True

        console = Console()
        with console.status(f"[bold yellow]({self.name}) Waiting for state: {target_state}...[/bold yellow]") as spinner:
            while True:
                curr_state = self.get_state()
                if curr_state == target_state:
                    break

                if target_state == "online" and curr_state == "boot-failed":
                    self.error(f"({self.name}) Boot sequence failed.")
                    return False

                time_passed = time.time() - start_time
                if seconds_int > 0 and time_passed >= seconds_int:
                    self.error(f"timed out after {seconds_int}s", no_eol=False, timestamp=False)
                    return False

                time.sleep(1)
                spinner.update(
                    f"[bold yellow]({self.name}) State: [bold cyan]{curr_state}[/bold cyan] -> "
                    f"Waiting for: [bold green]{target_state}[/bold green] "
                    f"({int(time_passed)}s / {seconds_int}s)...[/bold yellow]"
                )

        time_passed_int = int(time.time() - start_time)
        self.debug(f"State '{target_state}' reached after {time_passed_int}s")
        return True

    def wait_for_status(self, status: str, seconds: Union[int, float, str], dont_print: bool = False) -> bool:
        return self.wait_for_state(status, seconds, dont_print=dont_print)

    @staticmethod
    def format_state(state: str) -> str:
        if state == "online":
            return "[bold green]online[/bold green]"
        elif state == "offline":
            return "[dim red]offline[/dim red]"
        elif state in ("unresponsive", "unreachable", "boot-failed"):
            return f"[bold red]{state}[/bold red]"
        elif state == "stopping":
            return "[yellow]stopping[/yellow]"
        elif state.startswith("fetching-"):
            return f"[bold cyan]{state}[/bold cyan]"
        elif state == "nfs-mounted":
            return "[bold blue]nfs-mounted[/bold blue]"
        elif state in (
            "powering-on",
            "starting-vm",
            "power-cycling",
            "waiting-for-resources",
            "dhcp-bound",
            "ipxe-boot-config",
            "booting",
        ):
            return f"[bold yellow]{state}[/bold yellow]"
        return f"[cyan]{state}[/cyan]" if state else "[dim]-[/dim]"

    def is_job_stale(self) -> bool:
        if not self.job:
            return False

        try:
            pid = int(self.job_pid)
        except (ValueError, TypeError):
            pid = 0

        if pid <= 0:
            return True

        try:
            os.kill(pid, 0)
            return False
        except PermissionError:
            return False
        except (ProcessLookupError, OSError):
            return True

    def detect_and_clear_stale_job(self) -> bool:
        if self.is_job_stale():
            self.error(f"Detected stale job: {self.job} (PID: {self.job_pid})")
            self.clear_job()
            return True
        return False

    def print_info(self) -> None:
        Log.pause()

        console = Console()
        table = Table(
            title=f"Machine Information: {self.name}",
            header_style="bold cyan",
            border_style="bright_blue",
            title_style="bold white",
            show_header=True,
        )
        table.add_column("Property", style="bold yellow", width=20)
        table.add_column("Value", style="bold white")

        ip = self.get_ip() or "-"
        state_str = self.format_state(self.get_state())

        table.add_row("Name", self.name)
        table.add_row("MAC", self.mac)
        table.add_row("IP", ip)
        table.add_row("Is started", "[green]yes[/green]" if self.is_started else "[dim]no[/dim]")
        table.add_row("Is only VM", "[cyan]yes[/cyan]" if self.only_vm else "[dim]no[/dim]")
        table.add_row("OS", self.os or "-")
        table.add_row("Kernel", self.kernel or "-")
        table.add_row("State", state_str)

        if self.reservation:
            table.add_row("Reserved by", f"[bold magenta]{self.reservation}[/bold magenta]")

        nested = Machine.get_nested_resources(self.name)
        nested_str = f" [dim](nested: {nested})[/dim]" if nested else ""

        if self.resources:
            table.add_row("Resources", f"{self.resources}{nested_str}")
        if self.startcmd:
            table.add_row("Start command", self.startcmd)
        if self.stopcmd:
            table.add_row("Stop command", self.stopcmd)
        if self.powercmd:
            table.add_row("Power command", self.powercmd)
            table.add_row("Power usage", self.get_power())
        table.add_row("Start timeout", f"{self.start_timeout}s")
        table.add_row("Stop timeout", f"{self.stop_timeout}s")
        if self.boot_params:
            table.add_row("Boot params", self.boot_params)
        if self.vm_params:
            table.add_row("VM params", self.vm_params)

        vmlog_file = self.get_vmlog_path()
        if vmlog_file.exists():
            table.add_row("VM log", str(vmlog_file))

        vm_pid = self.get_vm_pid()
        if vm_pid:
            table.add_row("VM PID", str(vm_pid))

        if self.job:
            table.add_row("Running job", self.job)
            if self.job_pid:
                table.add_row("Job PID", self.job_pid)
            if self.is_job_stale():
                table.add_row("Job status", "[bold red]STALE (process not running)[/bold red]")

        try:
            queued = JobQueue.get_queue(self.name)
            if queued:
                table.add_row("Queued jobs", f"[bold yellow]{len(queued)} waiting[/bold yellow]")
        except Exception:
            pass

        console.print(table)
        Log.resume()

    def _set_os(self, val: Any) -> None:
        if isinstance(val, str) and "/" in val:
            parts = val.split("/", 1)
            arch, os_name = parts[0], parts[1]
        else:
            arch = getattr(val, "arch", "") if hasattr(val, "arch") else (val.get("arch", "") if isinstance(val, dict) else "")
            os_name = getattr(val, "os", "") if hasattr(val, "os") else (val.get("os", "") if isinstance(val, dict) else "")
        os_str = f"{arch}/{os_name}"
        if Os.is_runnable(arch, os_name, self):
            self.out(f"Setting OS {os_str}")
            self.os = os_str
            self.kernel = ""
            MachineRepository.update_fields(self.name, {"os": os_str, "kernel": ""})
            self.set("kernel", "latest")
        elif os_name == "NONE":
            self.out("Skipping ipxe. Machine will use next configured boot option.")
            self.os = "NONE/NONE"
            self.kernel = ""
            MachineRepository.update_fields(self.name, {"os": "NONE/NONE", "kernel": ""})
        else:
            fatal(f"Invalid OS: {os_str}")

    def _set_kernel(self, val: Any) -> None:
        kernels = self.get_kernels()
        val_str = str(val) if val is not None else ""
        if val_str.lower() == "latest":
            val_str = ""
            latest_date = ""
            for name, date in kernels.items():
                if date >= latest_date:
                    latest_date = date
                    val_str = name

        if val_str in kernels or val_str == "":
            initrd = pathlib.Path(config.MAMA_PATH) / "machines" / self.name / self.os / "boot" / f"initrd-{val_str}"
            if initrd.exists():
                if Util.is_root():
                    initrd.chmod(0o644)
                else:
                    subprocess.run(["sudo", "chmod", "644", str(initrd)])

            self.kernel = val_str
            MachineRepository.update_fields(self.name, {"kernel": val_str})
            self.out(f"Setting kernel to {self.kernel}")
        else:
            self.error(f"Kernel {val_str} doesn't exist. Aborting.")

    def set(self, attr: str, val: Any = "") -> None:
        def prompt_text(prompt_msg: str) -> str:
            res = questionary.text(prompt_msg).ask()
            return res.strip() if res is not None else ""

        if attr == "state":
            out(f"Current state: {self.state or '(none)'}")
            if not val:
                val = prompt_text("Enter new state:")
            val_str = str(val).strip()
            self.set_state(val_str)
            self.out(f"Setting state to: {val_str}")
        elif attr == "os":
            self._set_os(val)
        elif attr == "kernel":
            self._set_kernel(val)
        elif attr in self.SETTABLE_FIELDS:
            label, prompt_msg, field_name = self.SETTABLE_FIELDS[attr]
            curr_val = getattr(self, field_name, "")
            out(f"Current {label}: {curr_val if curr_val not in ('', None) else '(none)'}")

            if val is False or val == "":
                if field_name == "only_vm":
                    val = questionary.confirm("Is this only a virtual machine?").ask()
                else:
                    val = prompt_text(prompt_msg)

            if field_name == "boot_params":
                s = str(val).strip()
                new_val = "" if s.lower() in ("none", "clear", '""', "''") else s
                self.boot_params = new_val
                MachineRepository.update_fields(self.name, {"boot_params": new_val})
                self.out(f"Setting boot parameters to: {new_val if new_val else '(none)'}")
            elif field_name in ("start_timeout", "stop_timeout"):
                default = self.start_timeout_default if field_name == "start_timeout" else self.stop_timeout_default
                try:
                    new_val = int(val) if val and str(val).strip() else default
                except (ValueError, TypeError):
                    new_val = default
                setattr(self, field_name, new_val)
                MachineRepository.update_fields(self.name, {field_name: new_val})
            elif field_name == "only_vm":
                new_bool = to_bool(val)
                self.only_vm = new_bool
                MachineRepository.update_fields(self.name, {"only_vm": 1 if new_bool else 0})
                self.out(f"Setting only_vm to: {'yes' if new_bool else 'no'}")
            else:
                new_val = str(val)
                setattr(self, field_name, new_val)
                MachineRepository.update_fields(self.name, {field_name: new_val})
        else:
            self.error(f"Invalid attribute: {attr}")

    def get(self, attr: str) -> Any:
        attr_norm = attr.replace("-", "_")
        dispatch: Dict[str, Callable[[], Any]] = {
            "status": self.get_state,
            "state": self.get_state,
            "power": self.get_power,
            "ip": self.get_ip,
            "nfs_mounted": lambda: self.is_nfs_mounted(),
            "queue": lambda: len(JobQueue.get_queue(self.name)),
            "params": lambda: self.boot_params,
            "vmparams": lambda: self.vm_params,
            "os": lambda: self.os,
            "kernel": lambda: self.kernel,
            "resources": lambda: self.resources,
            "reservation": lambda: self.reservation,
            "mac": lambda: self.mac,
            "startcmd": lambda: self.startcmd,
            "stopcmd": lambda: self.stopcmd,
            "powercmd": lambda: self.powercmd,
            "start_timeout": lambda: self.start_timeout,
            "stop_timeout": lambda: self.stop_timeout,
            "is_started": lambda: self.is_started,
            "only_vm": lambda: self.only_vm,
        }
        getter = dispatch.get(attr_norm)
        if getter:
            return getter()

        self.error("Invalid attribute")
        return None

    def get_os_arch(self) -> str:
        if not self.os:
            return ""
        return self.os.split("/")[0]

    @staticmethod
    def get_nested_resources(mach_name: str) -> str:
        mach = Machine.get_by_name(mach_name)
        if not mach:
            return ""

        str_list: List[str] = []
        resources = [r.strip() for r in mach.resources.split(" ") if r.strip()]
        for resource in resources:
            str_list.append(resource)
            nested = Machine.get_nested_resources(resource)
            if nested:
                str_list.append(nested)

        return " ".join(str_list)

    def wait_for_resources(self, timeout: Optional[int] = None) -> bool:
        if timeout is None:
            timeout = self.resource_wait_timeout

        if not self.resources.strip():
            return True

        self.set_state("waiting-for-resources")

        console = Console()
        start_time = time.time()

        while True:
            resources = Machine.get_nested_resources(self.name).split()
            res_wait: List[str] = []

            machs = self.get_all()
            for mach in machs:
                if not mach.is_started or mach.name == self.name:
                    continue
                mach_res = mach.resources.split()
                for res in resources:
                    if res in mach_res and res not in res_wait:
                        res_wait.append(res)

            if not res_wait:
                break

            wait_str = ", ".join(res_wait)
            with console.status(f"[bold yellow]({self.name}) Waiting for locked resources: {wait_str}...[/bold yellow]"):
                time.sleep(10)

            if time.time() - start_time >= timeout:
                self.error("Timed out waiting for resources")
                return False

        self.out(f"Acquired resources: {self.resources}")
        return True

    def start(self) -> bool:
        curr_state = self.get_state()
        if curr_state == "online":
            self.error("Machine is already started")
            return False

        if not self.os:
            self.error("No OS has been configured!")
            return False

        parts = self.os.split("/")
        arch = parts[0]
        os_name = parts[1] if len(parts) > 1 else ""

        if not Os.is_runnable(arch, os_name, self) and os_name != "NONE":
            self.error(f"OS is not runnable: {self.os}")
            return False

        if not self.kernel and os_name != "NONE":
            self.error("No kernel set for machine")
            return False

        if curr_state in ("unresponsive", "unreachable"):
            self.set_state("power-cycling")
            self.stop()
            self.debug(f"Waiting for power cycle delay: {self.power_cycle_delay}")
            time.sleep(self.power_cycle_delay)
            return self.start()

        if self.only_vm:
            return self.start_vm()

        if not self.wait_for_resources():
            return False

        self.is_started = True
        MachineRepository.update_fields(self.name, {"_is_started": 1})

        if curr_state == "offline":
            self.set_state("powering-on")
            if self.startcmd:
                startcmd = self._preprocess_cmd(self.startcmd)
                self.debug(f"Start command: {startcmd}")
                subprocess.run(startcmd, shell=True)
        else:
            self.set_state("booting")

        effective_timeout = MachineRepository.get_calculated_start_timeout(
            self.name, self.os, "bm", default_timeout=self.start_timeout
        )
        self.debug(f"Using bare-metal boot timeout: {effective_timeout}s for OS {self.os}")

        t_start = time.time()
        ret = self.wait_for_state("online", effective_timeout)

        if ret:
            self.set_state("online")
            elapsed = int(time.time() - t_start)
            MachineRepository.record_start_time(self.name, self.os, "bm", elapsed, start_time=int(t_start))
            new_timeout = MachineRepository.get_calculated_start_timeout(self.name, self.os, "bm")
            self.set("start-timeout", new_timeout)
            self.debug(
                f"Bare-metal boot finished in {elapsed}s. Updated start timeout for {self.os} to {new_timeout}s."
            )
        else:
            self.set_state("boot-failed")
            self.stop()

        return ret

    def stop(self, force: bool = False) -> bool:
        if not self.is_started and not force:
            self.clear()
            return True

        self.out("Stopping machine")

        boot_type = "vm" if self.is_vm() else "bm"
        effective_timeout = MachineRepository.get_calculated_stop_timeout(
            self.name, self.os, boot_type, default_timeout=self.stop_timeout
        )
        self.debug(f"Using {boot_type.upper()} stop timeout: {effective_timeout}s for OS {self.os}")

        ssh_was_open = (self.get_ssh_status() == "open")
        self.set_state("stopping")
        t_stop_start = time.time()

        if ssh_was_open:
            shutdown_cmd = "halt" if (self.startcmd or self.stopcmd) else "poweroff"
            self.ssh_cmd(shutdown_cmd)

            halted = self.wait_for_state("offline", effective_timeout)
            if halted:
                elapsed = int(time.time() - t_stop_start)
                MachineRepository.record_stop_time(self.name, self.os, boot_type, elapsed, stop_time=int(time.time()))
                new_timeout = MachineRepository.get_calculated_stop_timeout(self.name, self.os, boot_type)
                self.set("stop-timeout", new_timeout)
                self.debug(
                    f"{boot_type.upper()} stop finished in {elapsed}s. Updated stop timeout for {self.os} to {new_timeout}s."
                )
            else:
                self.error(f"Graceful shutdown timed out after {effective_timeout}s. Forcing power off.")

        if self.stopcmd:
            stopcmd = self._preprocess_cmd(self.stopcmd)
            self.debug(f"Stop command: {stopcmd}")
            subprocess.run(stopcmd, shell=True)

        if self.is_vm():
            self.debug("Killing VM")
            self.kill_vm()

        self.clear()
        return True

    def is_ancestor(self, ancestor_pid: Union[int, str], pid: Optional[Union[int, str]] = None) -> bool:
        if pid is None:
            pid = os.getpid()

        try:
            ancestor_pid_int = int(ancestor_pid)
            pid_int = int(pid)
        except (ValueError, TypeError):
            return False

        if ancestor_pid_int == pid_int:
            return True

        try:
            with open(f"/proc/{pid_int}/stat", "r", encoding="utf-8") as f:
                parent_pid = int(f.read().split()[3])
        except (OSError, IndexError, ValueError):
            res = subprocess.run(["ps", "-p", str(pid_int), "-o", "ppid="], capture_output=True, text=True)
            if res.returncode != 0:
                fatal(f"Failed to look for ancestor with ps: pid={pid_int}")
            try:
                parent_pid = int(res.stdout.strip())
            except (ValueError, TypeError):
                parent_pid = 0

        if parent_pid == 0:
            return False

        if parent_pid == ancestor_pid_int:
            return True

        return self.is_ancestor(ancestor_pid_int, parent_pid)

    def is_idle(self) -> bool:
        if self.is_started:
            return False

        if self.job and not self.is_ancestor(self.job_pid):
            return False

        if self.job and self.is_ancestor(self.job_pid):
            error("NESTING JOBS!")

        return True

    def wait(self, reason: str = "wait") -> None:
        self.load()
        if not self.is_idle():
            started_str = "yes" if self.is_started else "no"
            job_str = f", job: {self.job}" if self.job else ""
            pid_str = f", job_pid: {self.job_pid}" if self.job_pid else ""
            self.out(f"Waiting for machine to become idle ( started: {started_str}{job_str}{pid_str})")

        console = Console()
        try:
            queue_ctx = JobQueue.track(self.name, reason)
        except Exception:
            queue_ctx = contextlib.nullcontext()

        with queue_ctx:
            while not self.is_idle():
                with console.status(f"[bold yellow]({self.name}) Waiting for machine to become idle...[/bold yellow]"):
                    time.sleep(10)
                    self.load()

    def ssh_cmd(self, cmd: str, no_timeout: bool = False) -> Union[int, bool]:
        if self.get_ssh_status() != "open":
            if not self.wait_for_state("online", 30, dont_print=True):
                self.error("Unable to execute ssh cmd since machine is not online.")
                return False

        self.debug(f"ssh: {cmd}")
        ip = self.get_ip()
        if not ip:
            self.error("No IP address available to execute ssh cmd.")
            return False

        logfile = getattr(Log, "logfile", False)
        return net_mgr.run_ssh_cmd(
            ip=ip,
            cmd=cmd,
            timeout_mins=self.ssh_cmd_timeout,
            no_timeout=no_timeout,
            logfile=logfile,
        )

    def reserve(self, user: str) -> None:
        self.reservation = user
        MachineRepository.update_fields(self.name, {"reservation": user})

    def get_kernels(self) -> Dict[str, str]:
        path = pathlib.Path(self.get_kernel_path())
        if not path.is_dir():
            return {}

        kernels: Dict[str, str] = {}
        try:
            for item in path.glob("initrd-*"):
                if not item.is_file():
                    continue
                kernel = item.name[len("initrd-") :]
                if kernel == "mama":
                    continue
                mtime = item.stat().st_mtime
                dt = datetime.datetime.fromtimestamp(mtime, tz=datetime.timezone.utc)
                kernels[kernel] = dt.strftime("%Y-%m-%d %H:%M:%S")
        except OSError:
            pass

        return dict(sorted(kernels.items(), key=lambda item: item[1]))

    def get_kernel_path(self) -> str:
        return os.path.join(config.MAMA_PATH, "machines", self.name, self.os, "boot", "")

    def _print_prefix(self, no_color: bool = False) -> str:
        s = f"{self.name}: {self.job}" if self.job else self.name
        if no_color:
            return f"({s})"
        else:
            return Util.string_to_color(f"({s})", 5)

    def out(self, msg: str, no_eol: bool = False, timestamp: bool = True) -> None:
        prefix = self._print_prefix()
        out(f"{prefix} {msg}", no_eol=no_eol, timestamp=timestamp)

    def debug(self, msg: str, no_eol: bool = False, timestamp: bool = True) -> None:
        prefix = self._print_prefix()
        debug(f"{prefix} {msg}", no_eol=no_eol, timestamp=timestamp)

    def error(self, msg: str, no_eol: bool = False, timestamp: bool = True) -> None:
        prefix = self._print_prefix(no_color=True)
        error(f"{prefix} {msg}", no_eol=no_eol, timestamp=timestamp)

    def fatal(self, msg: str, errno: int = 1) -> None:
        prefix = self._print_prefix()
        fatal(f"({prefix}) {msg}", errno=errno)