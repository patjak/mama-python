from __future__ import annotations

import contextlib
import datetime
import fcntl
import os
import pathlib
import subprocess
import time
from typing import Any, Dict, List, Optional, Union

from rich.console import Console
from rich.table import Table

try:
    import questionary
    HAS_QUESTIONARY = True
except ImportError:
    HAS_QUESTIONARY = False

import config
from logger import Log, debug, error, fatal, out
from options import Options
from os_mgr import Os
from repository import MachineRepository
from util import Util


class Machine:
    # Class tunables for machine management
    start_timeout_default: int = 4 * 60
    stop_timeout_default: int = 30
    ssh_cmd_timeout: int = 1 * 60
    power_cycle_delay: int = 30
    resource_wait_timeout: int = 20 * 60 * 60

    def __init__(self, name: str = "", mac: str = "") -> None:
        self.name: str = name
        self.mac: str = mac.lower()
        self.ip: str = ""
        self._is_started: str = "0"
        self.state: str = ""
        self.arch: str = ""
        self.os: str = ""
        self.kernel: str = ""
        self.reservation: str = ""
        self.resources: str = ""
        self.boot_params: str = ""
        self.vm_params: str = ""
        self.only_vm: Union[int, str, bool] = 0
        self.job: str = ""
        self.job_pid: str = ""
        self.startcmd: str = ""
        self.stopcmd: str = ""
        self.powercmd: str = ""
        self.start_timeout: int = self.start_timeout_default
        self.stop_timeout: int = self.stop_timeout_default

    def _preprocess_cmd(self, cmd: str) -> str:
        """Substitute parameters into startcmd, stopcmd, and powercmd."""
        cmd = cmd.replace("$OS_ARCH", self.get_os_arch())
        cmd = cmd.replace("$MAC", self.mac)
        cmd = cmd.replace("$PID", str(os.getpid()))
        cmd = cmd.replace("$MAMA_PATH", getattr(config, "MAMA_PATH", ""))
        cmd = cmd.replace("$MAMA_HOST", getattr(config, "MAMA_HOST", ""))
        return cmd

    @contextlib.contextmanager
    def lock(self):
        """Exclusive filesystem lock for mutating machine disk contents."""
        lock_file = pathlib.Path(config.MAMA_PATH) / "machines" / self.name / "lock"
        try:
            lock_file.parent.mkdir(parents=True, exist_ok=True)
            if not lock_file.exists():
                lock_file.touch(mode=0o666, exist_ok=True)
        except Exception:
            pass

        if getattr(config, "DEBUG_LOCK", False):
            msg = f"PID {os.getpid()}: {self.name}: Acquiring machine lock\n"
            lock_debug = pathlib.Path(config.MAMA_PATH) / "mama-lock-debug"
            try:
                with open(lock_debug, "a") as f:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                    f.write(msg)
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass

        with open(lock_file, "a+") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if getattr(config, "DEBUG_LOCK", False):
                    msg = f"PID {os.getpid()}: {self.name}: Releasing machine lock\n"
                    lock_debug = pathlib.Path(config.MAMA_PATH) / "mama-lock-debug"
                    try:
                        with open(lock_debug, "a") as f:
                            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                            f.write(msg)
                            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                    except Exception:
                        pass
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass

    @classmethod
    def get_all(cls) -> List[Machine]:
        """Query all machines directly from SQLite."""
        machs: List[Machine] = []
        rows = MachineRepository.get_all()
        for row in rows:
            mach = cls()
            mach.fill_from_dict(row)
            machs.append(mach)
        machs.sort(key=lambda x: x.name)
        return machs

    @classmethod
    def get_by_name(cls, name: str) -> Optional[Machine]:
        """Indexed point-lookup by machine name from SQLite."""
        row = MachineRepository.get_by_name(name)
        if row:
            mach = cls()
            mach.fill_from_dict(row)
            return mach
        return None

    @classmethod
    def find_by_mac(cls, mac: str) -> Optional[Machine]:
        """Indexed point-lookup by MAC address from SQLite."""
        row = MachineRepository.get_by_mac(mac)
        if row:
            mach = cls()
            mach.fill_from_dict(row)
            return mach
        return None

    def fill_from_dict(self, data: Dict[str, Any]) -> None:
        """Populate Machine attributes from a dictionary or SQLite row."""
        def get_val(key: str, default: str = "") -> str:
            val = data.get(key)
            if val is None and key == "_is_started":
                val = data.get("is_started")
            if val is not None:
                return str(val)
            return default

        self.name = get_val("name")
        self.mac = get_val("mac").lower()
        self.ip = get_val("ip")
        self._is_started = get_val("_is_started", "0")
        self.state = get_val("state")
        self.arch = get_val("arch")
        self.os = get_val("os")
        self.kernel = get_val("kernel")
        self.reservation = get_val("reservation")
        self.resources = get_val("resources")
        self.boot_params = get_val("boot_params")
        self.vm_params = get_val("vm_params")
        self.only_vm = get_val("only_vm", "0")
        self.job = get_val("job")
        self.job_pid = get_val("job_pid")
        self.startcmd = get_val("startcmd")
        self.stopcmd = get_val("stopcmd")
        self.powercmd = get_val("powercmd")

        start_tout = get_val("start_timeout")
        try:
            self.start_timeout = int(start_tout) if start_tout and str(start_tout).strip() else self.start_timeout_default
        except (ValueError, TypeError):
            self.start_timeout = self.start_timeout_default

        stop_tout = get_val("stop_timeout")
        try:
            self.stop_timeout = int(stop_tout) if stop_tout and str(stop_tout).strip() else self.stop_timeout_default
        except (ValueError, TypeError):
            self.stop_timeout = self.stop_timeout_default

    def fill_from_xmlobj(self, obj: Any) -> None:
        """Backwards compatibility wrapper for XML elements or dictionaries."""
        if isinstance(obj, dict):
            self.fill_from_dict(obj)
            return

        def get_val(key: str, default: str = "") -> str:
            if hasattr(obj, "find") and callable(getattr(obj, "find")):
                child = obj.find(key)
                if child is not None and child.text is not None:
                    return child.text
                return default
            elif hasattr(obj, key):
                val = getattr(obj, key)
                if val is not None and not callable(val):
                    return str(val)
                return default
            return default

        d = {
            "name": get_val("name"),
            "mac": get_val("mac"),
            "ip": get_val("ip"),
            "_is_started": get_val("_is_started") or get_val("is_started"),
            "state": get_val("state"),
            "arch": get_val("arch"),
            "os": get_val("os"),
            "kernel": get_val("kernel"),
            "reservation": get_val("reservation"),
            "resources": get_val("resources"),
            "boot_params": get_val("boot_params"),
            "vm_params": get_val("vm_params"),
            "only_vm": get_val("only_vm"),
            "job": get_val("job"),
            "job_pid": get_val("job_pid"),
            "startcmd": get_val("startcmd"),
            "stopcmd": get_val("stopcmd"),
            "powercmd": get_val("powercmd"),
            "start_timeout": get_val("start_timeout"),
            "stop_timeout": get_val("stop_timeout"),
        }
        self.fill_from_dict(d)

    def to_dict(self) -> Dict[str, Any]:
        """Convert machine attributes into dictionary representation for DB upserts."""
        return {
            "name": self.name,
            "mac": self.mac,
            "ip": self.ip,
            "state": self.state,
            "arch": self.arch,
            "os": self.os,
            "kernel": self.kernel,
            "reservation": self.reservation,
            "resources": self.resources,
            "boot_params": self.boot_params,
            "vm_params": self.vm_params,
            "only_vm": self.only_vm,
            "job": self.job,
            "job_pid": self.job_pid,
            "startcmd": self.startcmd,
            "stopcmd": self.stopcmd,
            "powercmd": self.powercmd,
            "start_timeout": self.start_timeout,
            "stop_timeout": self.stop_timeout,
            "_is_started": self._is_started,
        }

    def set_state(self, state: str) -> None:
        """Record the latest boot sequence state for this machine."""
        self.state = state
        MachineRepository.update_fields(self.name, {"state": state})
        self.debug(f"State: {state}")

    @classmethod
    def set_state_by_name(cls, name: str, state: str) -> None:
        """Update state directly by machine name."""
        if name:
            MachineRepository.update_fields(name, {"state": state})

    @classmethod
    def set_state_by_mac(cls, mac: str, state: str) -> None:
        """Update state directly by MAC address lookup."""
        if not mac:
            return
        mach = cls.find_by_mac(mac)
        if mach:
            mach.set_state(state)
        else:
            MachineRepository.update_fields(mac, {"state": state})

    def save(self) -> None:
        """Upsert complete machine configuration to SQLite."""
        MachineRepository.upsert_machine(self.to_dict())

    def load(self) -> None:
        """Reload machine properties from SQLite database."""
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
        """Atomically clear IP, started status, state, and boot_params in DB."""
        self.ip = ""
        self._is_started = "0"
        self.state = ""
        MachineRepository.update_fields(self.name, {"ip": "", "_is_started": "0", "state": ""})
        self.debug("cleared machine state")

    def clear_job(self) -> None:
        """Atomically clear job details in DB."""
        self.job = ""
        self.job_pid = ""
        MachineRepository.update_fields(self.name, {"job": "", "job_pid": ""})
        self.debug("cleared job")

    def get_ip_from_leases(self) -> Optional[str]:
        """Resolve IP address from active dnsmasq DHCP leases."""
        if not getattr(config, "DHCP_ENABLED", True):
            return None
        try:
            from dhcp import DnsmasqManager
            leases = DnsmasqManager.get_active_leases()
            return leases.get(self.mac.lower())
        except Exception:
            return None

    def get_ip_from_mac(self) -> Optional[str]:
        if not self.is_started():
            return None

        cmd = f'ip -4 neighbor | grep "{self.mac}"'
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if res.returncode == 0:
            for line in res.stdout.strip().splitlines():
                parts = [p for p in line.split() if p]
                if len(parts) >= 6:
                    ip, state = parts[0], parts[5]
                    if state == "REACHABLE" and Util.is_valid_ip(ip):
                        return ip
        return None

    def get_ip(self) -> Optional[str]:
        # 1. Check active dnsmasq DHCP leases
        ip = self.get_ip_from_leases()

        # 2. Fallback to neighbor ARP table
        if not ip:
            ip = self.get_ip_from_mac()

        if not ip:
            self.load()
            ip = self.ip if self.ip else None
        else:
            if self.ip != ip:
                self.debug(f"Changing IP {self.ip} -> {ip}")
                self.set_ip(ip)
                if self.is_started() and self.state in ("powering-on", "starting-vm", "power-cycling", ""):
                    self.set_state("dhcp-bound")

        return ip

    def set_ip(self, ip: str) -> None:
        if Util.is_valid_ip(ip) or ip == "":
            self.ip = ip
            MachineRepository.update_fields(self.name, {"ip": ip})

    def get_power(self) -> str:
        """Execute powercmd to query current power usage in Watts."""
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

    def get_ssh_status(self) -> str:
        ip = self.get_ip()
        if not ip:
            return "closed"

        cmd = f"nc -w 5 -z {ip} 22 2> /dev/null"
        res = subprocess.run(cmd, shell=True)
        return "open" if res.returncode == 0 else "closed"

    def is_started(self) -> bool:
        return str(self._is_started) in ("1", "True", "true")

    def is_only_vm(self) -> bool:
        return str(self.only_vm) in ("1", "True", "true")

    def get_status(self) -> str:
        ip = self.get_ip()
        if not ip:
            self.set_ip("")
            return "offline"
        else:
            self.set_ip(ip)

        cmd = f"ping -w 3 -c 1 {ip}"
        res = subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode == 0:
            if self.get_ssh_status() == "open":
                return "online"
            else:
                return "unreachable"

        self.set_ip("")
        return "offline"

    def is_job_stale(self) -> bool:
        if not self.job:
            return False

        try:
            pid = int(self.job_pid)
        except (ValueError, TypeError):
            pid = 0

        if pid <= 0:
            return True

        res = subprocess.run(f"ps -p {pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return res.returncode != 0

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
        status = self.get_status()
        if status == "online":
            status_str = "[bold green]online[/bold green]"
        elif status == "unreachable":
            status_str = "[bold yellow]unreachable[/bold yellow]"
        else:
            status_str = "[dim red]offline[/dim red]"

        table.add_row("Name", self.name)
        table.add_row("MAC", self.mac)
        table.add_row("IP", ip)
        table.add_row("Is started", "[green]yes[/green]" if self.is_started() else "[dim]no[/dim]")
        table.add_row("Is only VM", "[cyan]yes[/cyan]" if self.is_only_vm() else "[dim]no[/dim]")
        table.add_row("OS", self.os or "-")
        table.add_row("Kernel", self.kernel or "-")
        table.add_row("Status", status_str)
        table.add_row("State", f"[bold cyan]{self.state}[/bold cyan]" if self.state else "-")

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
        if self.job:
            table.add_row("Running job", self.job)
            if self.job_pid:
                table.add_row("Job PID", self.job_pid)
            if self.is_job_stale():
                table.add_row("Job status", "[bold red]STALE (process not running)[/bold red]")

        console.print(table)
        Log.resume()

    def set(self, attr: str, val: Any = "") -> None:
        def prompt_text(prompt_msg: str) -> str:
            if HAS_QUESTIONARY:
                try:
                    res = questionary.text(prompt_msg).ask()
                    return res if res is not None else ""
                except Exception:
                    return Util.get_line(f"{prompt_msg} ")
            return Util.get_line(f"{prompt_msg} ")

        if attr == "state":
            out(f"Current state: {self.state or '(none)'}")
            if not val:
                val = prompt_text("Enter new state:")
            val_str = str(val).strip()
            self.set_state(val_str)
            self.out(f"Setting state to: {val_str}")
        elif attr == "os":
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
        elif attr == "kernel":
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
                if not Util.is_root():
                    out("You must be root to run this command")

                initrd = pathlib.Path(config.MAMA_PATH) / "machines" / self.name / self.os / "boot" / f"initrd-{val_str}"
                if initrd.exists():
                    subprocess.run(f"sudo chmod 644 {initrd}", shell=True)

                self.kernel = val_str
                MachineRepository.update_fields(self.name, {"kernel": val_str})
                self.out(f"Setting kernel to {self.kernel}")
            else:
                self.error(f"Kernel {val_str} doesn't exist. Aborting.")
        elif attr == "resources":
            out(f"Current resources: {self.resources}")
            if not val:
                val = prompt_text("Enter new resources (space separated):")
            self.resources = str(val)
            MachineRepository.update_fields(self.name, {"resources": str(val)})
        elif attr == "params":
            out(f"Current boot parameters: {self.boot_params if self.boot_params else '(none)'}")
            if val is False:
                val = prompt_text("Enter new boot parameters:")

            val_str = str(val).strip()
            if val_str.lower() in ("none", "clear", '""', "''"):
                val_str = ""

            self.boot_params = val_str
            MachineRepository.update_fields(self.name, {"boot_params": val_str})
            self.out(f"Setting boot parameters to: {self.boot_params if self.boot_params else '(none)'}")
        elif attr == "vmparams":
            out(f"Current VM parameters: {self.vm_params}")
            if not val:
                val = prompt_text("Enter new VM parameters:")
            self.vm_params = str(val)
            MachineRepository.update_fields(self.name, {"vm_params": str(val)})
        elif attr == "startcmd":
            out(f"Current start command: {self.startcmd}")
            if not val:
                val = prompt_text("Enter new start command:")
            self.startcmd = str(val)
            MachineRepository.update_fields(self.name, {"startcmd": str(val)})
        elif attr == "stopcmd":
            out(f"Current stop command: {self.stopcmd}")
            if not val:
                val = prompt_text("Enter new stop command:")
            self.stopcmd = str(val)
            MachineRepository.update_fields(self.name, {"stopcmd": str(val)})
        elif attr == "powercmd":
            out(f"Current power command: {self.powercmd}")
            if not val:
                val = prompt_text("Enter new power command:")
            self.powercmd = str(val)
            MachineRepository.update_fields(self.name, {"powercmd": str(val)})
        elif attr == "start-timeout":
            if not val:
                out(f"Current start-timeout: {self.start_timeout}")
                val = prompt_text("Enter new start-timeout (leave empty for default):")
            tout = int(val) if val else self.start_timeout_default
            self.start_timeout = tout
            MachineRepository.update_fields(self.name, {"start_timeout": tout})
        elif attr in ("stop-timeout", "stop_timeout"):
            if not val:
                out(f"Current stop-timeout: {self.stop_timeout}")
                val = prompt_text("Enter new stop-timeout (leave empty for default):")
            tout = int(val) if val else self.stop_timeout_default
            self.stop_timeout = tout
            MachineRepository.update_fields(self.name, {"stop_timeout": tout})

    def get(self, attr: str) -> Any:
        mapping = {
            "state": self.state,
            "status": self.get_status(),
            "params": self.boot_params,
            "vmparams": self.vm_params,
            "os": self.os,
            "kernel": self.kernel,
            "resources": self.resources,
            "reservation": self.reservation,
            "ip": self.get_ip(),
            "mac": self.mac,
            "startcmd": self.startcmd,
            "stopcmd": self.stopcmd,
            "powercmd": self.powercmd,
            "power": self.get_power(),
            "start-timeout": self.start_timeout,
            "stop-timeout": self.stop_timeout,
        }
        if attr in mapping:
            return mapping[attr]
        self.error("Invalid attribute")
        return None

    def get_os_arch(self) -> str:
        if not self.os:
            return ""
        return self.os.split("/")[0]

    def wait_for_status(self, status: str, seconds: Union[int, float, str], dont_print: bool = False) -> bool:
        try:
            seconds_int = int(seconds) if seconds else 0
        except (ValueError, TypeError):
            seconds_int = 0

        start_time = time.time()

        if dont_print:
            while self.get_status() != status:
                time_passed = time.time() - start_time
                time.sleep(1)
                if seconds_int > 0 and time_passed >= seconds_int:
                    return False
            return True

        console = Console()
        with console.status(f"[bold yellow]({self.name}) Waiting for status: {status}...[/bold yellow]") as status_spinner:
            while self.get_status() != status:
                time_passed = time.time() - start_time
                if seconds_int > 0 and time_passed >= seconds_int:
                    self.error(f"timed out after {seconds_int}s", no_eol=False, timestamp=False)
                    return False
                time.sleep(1)
                status_spinner.update(
                    f"[bold yellow]({self.name}) Waiting for status: {status} ({int(time_passed)}s / {seconds_int}s)...[/bold yellow]"
                )

        time_passed_int = int(time.time() - start_time)
        self.debug(f"Status reached after {time_passed_int} s")
        return True

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
                if not mach.is_started() or mach.name == self.name:
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
        status = self.get_status()
        if status == "online":
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

        if status == "unreachable":
            self.set_state("power-cycling")
            self.stop()
            self.debug(f"Waiting for power cycle delay: {self.power_cycle_delay}")
            time.sleep(self.power_cycle_delay)
            return self.start()

        if self.is_only_vm():
            return self.start_vm()

        if not self.wait_for_resources():
            return False

        self._is_started = "1"
        MachineRepository.update_fields(self.name, {"_is_started": "1"})

        if status == "offline":
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
        ret = self.wait_for_status("online", effective_timeout)

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

    def is_vm(self) -> bool:
        if self.is_only_vm():
            return True

        cmd = f"ps aux | grep qemu | grep {self.mac}"
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        lines = [line for line in res.stdout.strip().splitlines() if line]
        return len(lines) > 1

    def kill_vm(self) -> Optional[bool]:
        if not self.is_vm():
            return False

        cmd = f"ps ax | grep qemu | grep -v SCREEN | grep {self.mac}"
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
        if not lines:
            return None

        pid = lines[0].split()[0]
        self.debug(f"Killing VM with pid {pid}")
        subprocess.run(f"sudo kill {pid}", shell=True)
        return True

    def start_vm(self) -> bool:
        if not Util.is_root():
            self.out("You must be root to start a VM")

        cmd_check = f"ps aux | grep qemu | grep {self.mac}"
        res = subprocess.run(cmd_check, shell=True, capture_output=True, text=True)
        lines = [line for line in res.stdout.strip().splitlines() if line]
        if len(lines) > 1:
            self.error("Failed to start VM. Already running.")
            return False

        self._is_started = "1"
        self.set_state("starting-vm")
        MachineRepository.update_fields(self.name, {"_is_started": "1"})

        self.out(f"Starting virtual machine with OS {self.os} and kernel {self.kernel}")

        try:
            num_cores = int(subprocess.check_output("nproc", shell=True).strip())
        except Exception:
            num_cores = 1

        cores_str = f"-smp {num_cores}"
        sys_str = f"-m {1024 * 44}"
        net_str = f"-net nic,model=virtio,macaddr={self.mac} -net bridge,br=br0"
        os_arch = self.os.split("/")[0] if "/" in self.os else self.os

        mama_arch = subprocess.check_output("uname -p", shell=True, text=True).strip()
        kvm_str = ""

        if os_arch == "aarch64":
            kernel_filename = f"Image-{self.kernel}"
        elif os_arch == "ppc64le":
            kernel_filename = f"vmlinux-{self.kernel}"
        else:
            kernel_filename = f"vmlinuz-{self.kernel}"
        initrd_filename = f"initrd-{self.kernel}"

        kernel = os.path.join(self.get_kernel_path(), kernel_filename)
        initrd = os.path.join(self.get_kernel_path(), initrd_filename)

        if os_arch == "i686":
            arch = "x86_64"
        elif os_arch == "aarch64":
            arch = "aarch64"
            sys_str += " -machine virt,gic-version=3 -cpu cortex-a72 -accel tcg,thread=multi,tb-size=1024 -bios /usr/share/qemu/qemu-uefi-aarch64.bin"
        elif os_arch == "ppc64le":
            arch = "ppc64"
            sys_str += " -M pseries"
        else:
            arch = "x86_64"
            sys_str += " -cpu host"

        net_str += (
            f' -kernel {kernel} -initrd {initrd} -append "{self.boot_params} '
            f'ip=dhcp rd.neednet=1 ifname=lan0:{self.mac} systemd.hostname={self.name} '
            f'root=nfs:{config.MAMA_HOST}:/machines/{self.name}/{self.os},rw,vers=4,nconnect=16 '
            f'console=ttyS0 console=tty"'
        )

        if arch == mama_arch:
            kvm_str = "-enable-kvm"

        cmd = (
            f"sudo screen -d -m qemu-system-{arch} {sys_str} {kvm_str} {cores_str} {net_str} "
            f"-nographic -serial mon:stdio {self.vm_params}"
        )
        self.debug(cmd)
        subprocess.run(cmd, shell=True)

        time.sleep(3)

        effective_timeout = MachineRepository.get_calculated_start_timeout(
            self.name, self.os, "vm", default_timeout=self.start_timeout
        )
        self.debug(f"Using VM boot timeout: {effective_timeout}s for OS {self.os}")

        t_start = time.time()
        ret = self.wait_for_status("online", effective_timeout)

        if ret:
            self.set_state("online")
            elapsed = int(time.time() - t_start)
            MachineRepository.record_start_time(self.name, self.os, "vm", elapsed, start_time=int(t_start))
            new_timeout = MachineRepository.get_calculated_start_timeout(self.name, self.os, "vm")
            self.set("start-timeout", new_timeout)
            self.debug(
                f"VM boot finished in {elapsed}s. Updated start timeout for {self.os} to {new_timeout}s."
            )
        else:
            self.set_state("boot-failed")
            self.kill_vm()
            self.clear()

        return ret

    def stop(self, force: bool = False) -> bool:
        if not self.is_started() and self.get_status() == "offline" and not force:
            return True

        self.out("Stopping machine")
        self.set_state("stopping")

        boot_type = "vm" if self.is_vm() else "bm"
        effective_timeout = MachineRepository.get_calculated_stop_timeout(
            self.name, self.os, boot_type, default_timeout=self.stop_timeout
        )
        self.debug(f"Using {boot_type.upper()} stop timeout: {effective_timeout}s for OS {self.os}")

        status = self.get_status()
        t_stop_start = time.time()

        if status == "online":
            shutdown_cmd = "halt" if (self.startcmd or self.stopcmd) else "poweroff"
            self.ssh_cmd(shutdown_cmd)

        if self.wait_for_status("offline", effective_timeout):
            if status == "online":
                elapsed = int(time.time() - t_stop_start)
                MachineRepository.record_stop_time(self.name, self.os, boot_type, elapsed, stop_time=int(time.time()))
                new_timeout = MachineRepository.get_calculated_stop_timeout(self.name, self.os, boot_type)
                self.set("stop-timeout", new_timeout)
                self.debug(
                    f"{boot_type.upper()} stop finished in {elapsed}s. Updated stop timeout for {self.os} to {new_timeout}s."
                )

            if self.stopcmd:
                stopcmd = self._preprocess_cmd(self.stopcmd)
                self.debug(f"Stop command: {stopcmd}")
                subprocess.run(stopcmd, shell=True)

            if self.is_vm():
                self.debug("Killing VM")
                self.kill_vm()

        if self.is_vm():
            self.kill_vm()

        self.set_state("offline")
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

        cmd = f"ps -p {pid_int} -o ppid="
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
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
        if self.is_started():
            return False

        if self.job and not self.is_ancestor(self.job_pid):
            return False

        if self.job and self.is_ancestor(self.job_pid):
            error("NESTING JOBS!")

        return True

    def wait(self) -> None:
        self.load()
        if not self.is_idle():
            started_str = "yes" if self.is_started() else "no"
            job_str = f", job: {self.job}" if self.job else ""
            pid_str = f", job_pid: {self.job_pid}" if self.job_pid else ""
            self.out(f"Waiting for machine to become idle ( started: {started_str}{job_str}{pid_str})")

        console = Console()
        while not self.is_idle():
            with console.status(f"[bold yellow]({self.name}) Waiting for machine to become idle...[/bold yellow]"):
                time.sleep(10)
                self.load()

    def ssh_cmd(self, cmd: str) -> Union[int, bool]:
        no_timeout = getattr(Options, "options", {}).get("no-ssh-timeout", False)
        timeout_str = "" if no_timeout else f"timeout --foreground {self.ssh_cmd_timeout}m"

        if self.wait_for_status("online", 30, dont_print=True):
            self.debug(f"ssh: {cmd}")
            logfile = getattr(Log, "logfile", False)
            log_str = f" &>> {logfile}" if logfile else ""

            cmd_escaped = cmd.replace("$", "\\$")
            ip = self.get_ip()

            full_cmd = (
                f'{timeout_str} ssh -q -o "UserKnownHostsFile=/dev/null" '
                f'-o "ConnectTimeout=10" -o "StrictHostKeyChecking=no" '
                f'root@{ip} -t "{cmd_escaped}"{log_str}'
            )

            res = subprocess.run(full_cmd, shell=True)
            return res.returncode
        else:
            self.error("Unable to execute ssh cmd since machine is not online.")
            return False

    def reserve(self, user: str) -> None:
        self.reservation = user
        MachineRepository.update_fields(self.name, {"reservation": user})

    def get_kernels(self) -> Dict[str, str]:
        path = self.get_kernel_path()
        cmd = f"find {path} -maxdepth 1 2> /dev/null | grep initrd-"
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        rows = [r.strip() for r in res.stdout.splitlines() if r.strip()]

        kernels: Dict[str, str] = {}
        for row in rows:
            if not row.startswith(path):
                continue
            filename = row[len(path):]
            if not filename.startswith("initrd-"):
                continue
            kernel = filename[len("initrd-"):]
            if kernel == "mama":
                continue

            stat_res = subprocess.run(f"stat -c %Y {row}", shell=True, capture_output=True, text=True)
            if stat_res.returncode == 0 and stat_res.stdout.strip():
                epoch = int(stat_res.stdout.strip())
                dt = datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc)
                kernels[kernel] = dt.strftime("%Y-%m-%d %H:%M:%S")

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