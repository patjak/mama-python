from __future__ import annotations

import datetime
import fcntl
import os
import pathlib
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Union

import config
from logger import Log, debug, error, fatal, out
from options import Options
from services import Os, Settings, Util


def LOCK() -> None:
    Settings.lock()


def UNLOCK() -> None:
    Settings.unlock()


def IS_LOCKED() -> bool:
    return Settings.is_lock_held()


def SLEEP_ON_LOCK(seconds: int) -> None:
    UNLOCK()
    if IS_LOCKED():
        fatal("Sleeping while lock is held")
    time.sleep(seconds)
    LOCK()


class Machine:
    # Class tunables for machine management
    start_timeout_default: int = 4 * 60
    stop_timeout: int = 30
    ssh_cmd_timeout: int = 1 * 60
    power_cycle_delay: int = 30
    resource_wait_timeout: int = 20 * 60 * 60

    # Thread-safe reentrant lock registry per machine
    _machine_rlocks: Dict[str, threading.RLock] = {}
    _rlock_registry_lock = threading.Lock()

    @classmethod
    def _get_rlock(cls, machine_name: str) -> threading.RLock:
        """Fetch or create a reentrant lock dedicated to a specific machine name."""
        with cls._rlock_registry_lock:
            if machine_name not in cls._machine_rlocks:
                cls._machine_rlocks[machine_name] = threading.RLock()
            return cls._machine_rlocks[machine_name]

    def __init__(self, name: str = "", mac: str = "") -> None:
        self.name: str = name
        self.mac: str = mac.lower()
        self.ip: str = ""
        self._is_started: str = "0"
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

        self._lock: int = 0
        self._lock_stream: Optional[Any] = None

    # Acquire exclusive lock on the machine directory
    def lock(self) -> bool:
        if getattr(config, "DEBUG_LOCK", False):
            msg = f"PID {os.getpid()}: {self.name}: Acquiring machine lock: {self._lock + 1}\n"
            lock_debug = pathlib.Path(config.MAMA_PATH) / "mama-lock-debug"
            with open(lock_debug, "a") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                f.write(msg)

        # 1. Acquire thread-level reentrant lock for this specific machine name
        rlock = Machine._get_rlock(self.name)
        rlock.acquire()

        # 2. Acquire process-level file lock on initial outer lock call
        if self._lock == 0:
            if self._lock_stream is not None:
                rlock.release()
                fatal("Tried to acquire an already acquired lock")

            lock_file = pathlib.Path(config.MAMA_PATH) / "machines" / self.name / "lock"
            try:
                self._lock_stream = open(lock_file, "a+")
                fcntl.flock(self._lock_stream.fileno(), fcntl.LOCK_EX)
            except Exception as e:
                rlock.release()
                fatal(f"Failed to acquire lock on {lock_file}: {e}")

        self._lock += 1
        return True

    # Release exclusive lock on the machine directory
    def unlock(self) -> None:
        if getattr(config, "DEBUG_LOCK", False):
            msg = f"PID {os.getpid()}: {self.name}: Releasing machine lock: {self._lock}\n"
            lock_debug = pathlib.Path(config.MAMA_PATH) / "mama-lock-debug"
            with open(lock_debug, "a") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                f.write(msg)

        if self._lock == 0:
            fatal(f"{self.name} is not locked")

        self._lock -= 1

        # 1. Release process-level file lock when outermost lock releases
        if self._lock == 0 and self._lock_stream is not None:
            try:
                fcntl.flock(self._lock_stream.fileno(), fcntl.LOCK_UN)
                self._lock_stream.close()
            finally:
                self._lock_stream = None

        # 2. Release thread-level reentrant lock
        rlock = Machine._get_rlock(self.name)
        rlock.release()

    def is_locked(self) -> bool:
        return self._lock != 0

    def sleep_on_lock(self, seconds: int) -> None:
        self.unlock()
        if self.is_locked():
            fatal(f"Sleeping while lock is held on {self.name}")
        time.sleep(seconds)
        self.lock()

    @classmethod
    def get_all(cls) -> List[Machine]:
        machs: List[Machine] = []
        Settings.load()
        
        if Settings.settings is not None:
            if hasattr(Settings.settings, "findall"):
                machines_xml = Settings.settings.findall("machine")
            else:
                machines_xml = getattr(Settings.settings, "machine", [])
        else:
            machines_xml = []

        for m in machines_xml:
            mach = cls()
            mach.fill_from_xmlobj(m)
            machs.append(mach)

        machs.sort(key=lambda x: x.name)
        return machs

    @classmethod
    def get_by_name(cls, name: str) -> Optional[Machine]:
        for mach in cls.get_all():
            if mach.name == name:
                return mach
        return None

    @classmethod
    def find_by_mac(cls, mac: str) -> Optional[Machine]:
        mac_lower = mac.lower()
        for mach in cls.get_all():
            if mach.mac.lower() == mac_lower:
                return mach
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
        ip = self.get_ip_from_mac()

        LOCK()
        if not ip:
            self.load()
            if self.ip:
                ip = self.ip
            else:
                ip = None
        else:
            if self.ip != ip:
                self.debug(f"Changing IP {self.ip} -> {ip}")
                self.ip = ip
                self.save()
        UNLOCK()

        return ip

    def set_ip(self, ip: str) -> None:
        if Util.is_valid_ip(ip):
            LOCK()
            self.load()
            self.ip = ip
            self.save()
            UNLOCK()

    def get_power(self) -> str:
        """Execute powercmd to query current power usage in Watts."""
        if not self.powercmd or not self.powercmd.strip():
            return "-"

        cmd = self.powercmd
        cmd = cmd.replace("$OS_ARCH", self.get_os_arch())
        cmd = cmd.replace("$MAC", self.mac)
        cmd = cmd.replace("$PID", str(os.getpid()))

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

    def fill_from_xmlobj(self, obj: Any) -> None:
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
            elif isinstance(obj, dict) and key in obj:
                val = obj[key]
                if val is not None and not callable(val):
                    return str(val)
                return default
            return default

        self.name = get_val("name")
        self.mac = get_val("mac").lower()
        self.ip = get_val("ip")

        is_started_raw = get_val("_is_started") or get_val("is_started")
        self._is_started = is_started_raw if is_started_raw else "0"

        self.arch = get_val("arch")
        self.os = get_val("os")
        self.kernel = get_val("kernel")
        self.reservation = get_val("reservation")
        self.resources = get_val("resources")
        self.boot_params = get_val("boot_params")
        self.vm_params = get_val("vm_params")
        self.only_vm = get_val("only_vm")
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

    def save(self) -> None:
        Settings.update_machine(self)

    def load(self) -> None:
        for mach in Machine.get_all():
            if self.mac == mach.mac:
                self.fill_from_xmlobj(mach)
                return
        fatal("Failed to load machine")

    def clear(self) -> None:
        LOCK()
        self.load()
        self.ip = ""
        self._is_started = ""
        self.save()
        UNLOCK()
        self.debug("cleared machine state")

    def clear_job(self) -> None:
        LOCK()
        self.load()
        self.job = ""
        self.job_pid = ""
        self.save()
        UNLOCK()
        self.debug("cleared job")

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

        ip = self.get_ip() or ""
        out(f"Name:\t\t{self.name}")
        out(f"MAC:\t\t{self.mac}")
        out(f"IP:\t\t{ip}")
        out(f"Is started:\t{'yes' if self.is_started() else 'no'}")
        out(f"Is only VM:\t{'yes' if self.is_only_vm() else 'no'}")
        out(f"OS:\t\t{self.os}")
        out(f"Kernel:\t\t{self.kernel}")
        out(f"Status:\t\t{self.get_status()}")
        if self.reservation:
            out(f"Reserved by:\t{self.reservation}")

        nested = Machine.get_nested_resources(self.name)
        nested_str = f"(nested: {nested})" if nested else ""

        if self.resources:
            out(f"Resources:\t{self.resources} {nested_str}")
        if self.startcmd:
            out(f"Start command:\t{self.startcmd}")
        if self.stopcmd:
            out(f"Stop command:\t{self.stopcmd}")
        if self.powercmd:
            out(f"Power command:\t{self.powercmd}")
            out(f"Power usage:\t{self.get_power()}")
        out(f"Start timeout:\t{self.start_timeout}")
        out(f"Boot params:\t{self.boot_params}")
        if self.vm_params:
            out(f"VM params:\t{self.vm_params}")
        if self.job:
            out(f"Running job:\t{self.job}")
            if self.job_pid:
                out(f"Job PID:\t{self.job_pid}")
            if self.is_job_stale():
                out("Job status:\tSTALE (process not running)")

        Log.resume()

    def set(self, attr: str, val: Any = "") -> None:
        if attr == "os":
            arch = getattr(val, "arch", "") if hasattr(val, "arch") else (val.get("arch", "") if isinstance(val, dict) else "")
            os_name = getattr(val, "os", "") if hasattr(val, "os") else (val.get("os", "") if isinstance(val, dict) else "")
            os_str = f"{arch}/{os_name}"
            if Os.is_runnable(arch, os_name, self):
                LOCK()
                self.load()
                self.out(f"Setting OS {os_str}")
                self.os = os_str
                self.kernel = ""
                self.save()
                UNLOCK()
                self.set("kernel", "latest")
            elif os_name == "NONE":
                LOCK()
                self.load()
                self.out("Skipping ipxe. Machine will use next configured boot option.")
                self.os = "NONE/NONE"
                self.kernel = ""
                self.save()
                UNLOCK()
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

                LOCK()
                self.load()
                self.kernel = val_str
                self.save()
                UNLOCK()
                self.out(f"Setting kernel to {self.kernel}")
            else:
                self.error(f"Kernel {val_str} doesn't exist. Aborting.")
        elif attr == "resources":
            out(f"Current resources: {self.resources}")
            if not val:
                val = Util.get_line("Enter new resources (space separated): ")
            LOCK()
            self.load()
            self.resources = str(val)
            self.save()
            UNLOCK()
        elif attr == "params":
            out(f"Current boot parameters: {self.boot_params}")
            if not val:
                val = Util.get_line("Enter new boot parameters: ")
            LOCK()
            self.load()
            self.boot_params = str(val)
            self.save()
            UNLOCK()
        elif attr == "vmparams":
            out(f"Current VM parameters: {self.vm_params}")
            if not val:
                val = Util.get_line("Enter new VM parameters: ")
            LOCK()
            self.load()
            self.vm_params = str(val)
            self.save()
            UNLOCK()
        elif attr == "startcmd":
            out(f"Current start command: {self.startcmd}")
            if not val:
                val = Util.get_line("Enter new start command: ")
            LOCK()
            self.load()
            self.startcmd = str(val)
            self.save()
            UNLOCK()
        elif attr == "stopcmd":
            out(f"Current stop command: {self.stopcmd}")
            if not val:
                val = Util.get_line("Enter new stop command: ")
            LOCK()
            self.load()
            self.stopcmd = str(val)
            self.save()
            UNLOCK()
        elif attr == "powercmd":
            out(f"Current power command: {self.powercmd}")
            if not val:
                val = Util.get_line("Enter new power command: ")
            LOCK()
            self.load()
            self.powercmd = str(val)
            self.save()
            UNLOCK()
        elif attr == "start-timeout":
            out(f"Current start-timeout: {self.start_timeout}")
            if not val:
                val = Util.get_line("Enter new start-timeout (leave empty for default): ")
            LOCK()
            self.load()
            self.start_timeout = int(val) if val else self.start_timeout_default
            self.save()
            UNLOCK()

    def get(self, attr: str) -> Any:
        mapping = {
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

        if not dont_print:
            self.debug(f"Waiting for status: {status}... ")

        start_time = time.time()
        while self.get_status() != status:
            time_passed = time.time() - start_time
            time.sleep(1)
            if time_passed >= seconds_int:
                self.error(f"timed out after {seconds_int}s", no_eol=False, timestamp=False)
                return False

        time_passed_int = int(time.time() - start_time)
        if not dont_print:
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

        resources = Machine.get_nested_resources(self.name).split()

        sleep = False
        machs = self.get_all()
        res_wait: List[str] = []

        for mach in machs:
            if not mach.is_started():
                continue
            if mach.name == self.name:
                continue

            mach_res = mach.resources.split()
            for res in resources:
                if res in mach_res:
                    sleep = True
                    if res not in res_wait:
                        res_wait.append(res)

        if sleep:
            if timeout == self.resource_wait_timeout:
                self.out("Waiting for resources: ", no_eol=True)
                for res in res_wait:
                    out(f"{res} ", no_eol=True, timestamp=False)
                out("", no_eol=False, timestamp=False)

            SLEEP_ON_LOCK(10)

            timeout -= 10
            if timeout <= 0:
                self.error("Timed out waiting for resources")
                return False

            return self.wait_for_resources(timeout)

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
            self.stop()
            self.debug(f"Waiting for power cycle delay: {self.power_cycle_delay}")
            time.sleep(self.power_cycle_delay)
            return self.start()

        if self.is_only_vm():
            return self.start_vm()

        LOCK()
        if not self.wait_for_resources():
            UNLOCK()
            return False

        self.load()
        self._is_started = "1"
        self.save()
        UNLOCK()

        if status == "offline":
            if self.startcmd:
                startcmd = self.startcmd
                startcmd = startcmd.replace("$OS_ARCH", self.get_os_arch())
                startcmd = startcmd.replace("$MAC", self.mac)
                startcmd = startcmd.replace("$PID", str(os.getpid()))
                self.debug(f"Start command: {startcmd}")
                subprocess.run(startcmd, shell=True)

        ret = self.wait_for_status("online", self.start_timeout)

        if self.get_status() != "online":
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

        LOCK()

        cmd_check = f"ps aux | grep qemu | grep {self.mac}"
        res = subprocess.run(cmd_check, shell=True, capture_output=True, text=True)
        lines = [line for line in res.stdout.strip().splitlines() if line]
        if len(lines) > 1:
            self.error("Failed to start VM. Already running.")
            UNLOCK()
            return False

        LOCK()
        self.load()
        self._is_started = "1"
        self.save()
        UNLOCK()

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
        UNLOCK()

        ret = self.wait_for_status("online", self.start_timeout)
        if not ret:
            self.kill_vm()
            self.clear()

        return ret

    def stop(self, force: bool = False) -> bool:
        LOCK()
        self.load()
        if not self.is_started() and self.get_status() == "offline" and not force:
            UNLOCK()
            return True
        UNLOCK()

        self.out("Stopping machine")

        status = self.get_status()

        if status == "online":
            self.ssh_cmd("poweroff")

        if self.wait_for_status("offline", self.stop_timeout):
            if self.stopcmd:
                stopcmd = self.stopcmd
                stopcmd = stopcmd.replace("$OS_ARCH", self.get_os_arch())
                stopcmd = stopcmd.replace("$MAC", self.mac)
                stopcmd = stopcmd.replace("$PID", str(os.getpid()))

                self.debug(f"Stop command: {stopcmd}")
                subprocess.run(stopcmd, shell=True)

            if self.is_vm():
                self.debug("Killing VM")
                self.kill_vm()

        if self.is_vm():
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
        LOCK()
        self.load()
        if not self.is_idle():
            started_str = "yes" if self.is_started() else "no"
            job_str = f", job: {self.job}" if self.job else ""
            pid_str = f", job_pid: {self.job_pid}" if self.job_pid else ""
            self.out(f"Waiting for machine to become idle ( started: {started_str}{job_str}{pid_str})")

        while not self.is_idle():
            SLEEP_ON_LOCK(10)
            self.load()
        UNLOCK()

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
        LOCK()
        self.load()
        self.reservation = user
        self.save()
        UNLOCK()

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