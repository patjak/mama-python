from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
from typing import Any, List, Optional, Tuple, Union

# Environment & helper stubs
import config
from job import Job
from logger import Log, debug, error, fatal, out
from machine import Machine
from options import Options
from os_mgr import Arch, Os
from services import Settings, Util


def print_usage(argv: List[str]) -> None:
    usage = """Commands:
list                                            - list configured machines
list-os [machine]                               - list available OSes
list-kernel [machine]                           - list available kernels
list-resources                                  - list all resources and what is using them
log [machine]                                   - print log file
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
set <machine>   os <os>                         - set os for machine
                kernel <kernel>                 - set kernel for machine
                resources <resources>           - comma separated list of resources to set
                params <boot parameters>        - Set additional kernel command line parameters
                vmparams <VM parameters>        - Add additional QEMU command line parameters
                startcmd <command>              - Run a command instead of the normal start routine
                stopcmd <command>               - Run a command after normal halt/poweroff of machine
                powercmd <command>              - Run a command to fetch current power usage in Watts
get <machine> [os | params | resources | kernel - Get machine attribute
               reservation | ip | mac | vmparams |
               startcmd | stopcmd | powercmd | power ]
reserve <machine>                               - Reserve the machine for the current user
release <machine>                               - Release any reservation you have on the machine
release-forced <machine>                        - Release the machine even if you didn't reserve it
wait <machine>                                  - Wait for a machine to become available for use
serve                                           - start the iPXE boot server
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
        cmd_list()
        selected = Util.ask_from_array(machs, "Machine: ")
        if not selected:
            return False
        m_found = selected

    mach = Machine()
    if m_found:
        mach.fill_from_xmlobj(m_found)
        Log.set_file(mach.name)

    return mach


def select_os(mach: Machine) -> Os:
    path = os.path.join(config.MAMA_PATH, "machines", mach.name, "")
    oses = Util.get_directory_contents(path, 2)

    out("No\tArch\t\tOS")
    out("-" * 80)

    oses.insert(0, "NONE/NONE")

    for i, os_item in enumerate(oses, start=1):
        parts = os_item.split("/", 1)
        arch = parts[0]
        os_name = parts[1] if len(parts) > 1 else ""
        out(Util.pad_str(f"{i} ", 8), no_eol=True)
        out(Util.pad_str(arch, 16), no_eol=True)
        out(Util.pad_str(os_name, 32))

    out('\n* "NONE" will let the machine use next boot option after PXE')

    ask = Util.ask_from_array(oses, "OS:")
    os_obj = Os()
    if ask:
        parts = str(ask).split("/", 1)
        os_obj.arch = parts[0]
        os_obj.os = parts[1] if len(parts) > 1 else ""

    return os_obj


def list_kernels(mach: Machine) -> None:
    kernels = mach.get_kernels()

    out("No\tKernel\t\t\t\t\t\tLast modified")
    out("-" * 80)

    i = 1
    for kernel, date_str in kernels.items():
        out(Util.pad_str(f"{i} ", 8), no_eol=True)
        out(Util.pad_str(kernel, 48), no_eol=True)
        out(Util.pad_str(date_str, 20))
        i += 1

    out(Util.pad_str(f"{i} ", 8), no_eol=True)
    out(Util.pad_str("Latest", 48))


def select_kernel(mach: Machine) -> str:
    list_kernels(mach)

    kernels = mach.get_kernels()
    names: List[str] = []

    latest = ""
    latest_date = ""

    for name, date_str in kernels.items():
        if date_str >= latest_date:
            latest_date = date_str
            latest = name
        names.append(name)

    names.append("Latest")

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
    i = 1
    out(Util.pad_str("No", 4), no_eol=True)
    out(Util.pad_str("Name", 18), no_eol=True)
    out(Util.pad_str("IP", 16), no_eol=True)
    out(Util.pad_str("MAC", 18), no_eol=True)
    out(Util.pad_str("State", 8), no_eol=True)
    out(Util.pad_str("On", 5), no_eol=True)
    out(Util.pad_str("Pwr", 6), no_eol=True)
    out(Util.pad_str("VM", 4), no_eol=True)
    out(Util.pad_str("Job", 32))

    out("-" * 111)

    machs = Machine.get_all()

    for mach in machs:
        ip = mach.get_ip() or ""

        out(Util.pad_str(i, 4), no_eol=True)
        out(Util.pad_str(mach.name, 18), no_eol=True)
        out(Util.pad_str(ip, 16), no_eol=True)
        out(Util.pad_str(mach.mac, 18), no_eol=True)
        out(Util.pad_str(mach.get_status(), 8), no_eol=True)

        started = "Yes" if mach.is_started() else ""
        out(Util.pad_str(started, 5), no_eol=True)
        out(Util.pad_str(mach.get_power(), 6), no_eol=True)
        out(Util.pad_str("Yes" if mach.is_vm() else "", 4), no_eol=True)

        job_str = mach.job
        if job_str and mach.is_job_stale():
            job_str += " [STALE]"
        out(job_str)

        i += 1


def cmd_new() -> None:
    name = ""
    while not name:
        name = Util.get_line("Name: ").strip()
        machs = Machine.get_all()
        for m in machs:
            if m.name == name:
                error("Name already exists")
                name = ""
                break

    m = Machine()

    mac = ""
    while not Util.is_valid_mac(mac):
        mac = Util.get_line("MAC: ").strip()

    archs = Arch.get_buildable()
    out("\nNo\tArch")
    out("-" * 32)

    for idx, a in enumerate(archs, start=1):
        out(Util.pad_str(idx, 8), no_eol=True)
        out(a)

    arch = Util.ask_from_array(archs, "Arch")
    out(f"Arch selected: {arch}\n")

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

    Settings.add_machine(m)
    out("New machine created")


def cmd_list_os(m: Union[str, bool] = False) -> None:
    if m:
        mach = select_machine(m)
        if not isinstance(mach, Machine):
            return
        out(f"Listing installed OSes for machine: {mach.name}")
        path = os.path.join(config.MAMA_PATH, "machines", mach.name, "")
    else:
        path = os.path.join(config.MAMA_PATH, "os", "")

    out("No\tOS")
    out("-" * 80)

    oses = Util.get_directory_contents(path, 2)
    for idx, os_item in enumerate(oses, start=1):
        out(Util.pad_str(f"{idx} ", 8), no_eol=True)
        out(os_item)


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

    for res in sorted(resources.keys()):
        out(f"{res}:\t{resources[res]}")


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

    subprocess.run(f"{need_sudo} rm -Rf {tmp_dir}", shell=True)

    cmd = f"{need_sudo} kiwi-ng --debug system prepare --description {desc} --root {tmp_dir}{packages_str}"
    out(cmd)
    res = subprocess.run(cmd, shell=True)
    if res.returncode != 0:
        error("Failed to build os with kiwi")
        subprocess.run(f"{need_sudo} rm -Rf {tmp_dir}", shell=True)
        return

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

    # Find kernel version for dracut
    res_k = subprocess.run(f"ls {tmp_dir / 'boot' / 'config-*'}", shell=True, capture_output=True, text=True)
    if res_k.returncode != 0:
        error("Failed to find /boot/config-*")
        subprocess.run(f"{need_sudo} umount {tmp_dir / 'sys'}", shell=True)
        subprocess.run(f"{need_sudo} umount {tmp_dir / 'proc'}", shell=True)
        subprocess.run(f"{need_sudo} rm -Rf {tmp_dir}", shell=True)
        return

    kver = res_k.stdout.strip().split("config-")[1].splitlines()[0]

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

    res = subprocess.run(f"{need_sudo} rm -Rf {dst}", shell=True)
    if res.returncode != 0:
        fatal("Failed to remove previous installation of the OS")

    res = subprocess.run(f"{need_sudo} mkdir -p {dst.parent}", shell=True)
    if res.returncode != 0:
        fatal(f"Failed to create directory {dst}")

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

    src_mach.lock()
    dst_mach.lock()

    out(f"Copying os {arch}/{os_name} from {src_mach.name} to {dst_mach.name}")
    src_dir = pathlib.Path(config.MAMA_PATH) / "machines" / src_mach.name / arch / os_name
    dst_arch_dir = pathlib.Path(config.MAMA_PATH) / "machines" / dst_mach.name / arch / ""

    subprocess.run(f"{need_sudo} rm -Rf {dst_os_dir}", shell=True)
    subprocess.run(f"{need_sudo} mkdir -p {dst_os_dir}", shell=True)
    subprocess.run(f"{need_sudo} cp -r --reflink=auto {src_dir} {dst_arch_dir}", shell=True)

    dst_mach.unlock()
    src_mach.unlock()


def cmd_delete(arg: Union[str, bool]) -> None:
    mach = select_machine(arg)
    if isinstance(mach, Machine):
        name = mach.name
        if not name:
            error("Machine name is NULL")
            return
        Settings.delete_machine(mach)
        path = pathlib.Path(config.MAMA_PATH) / "machines" / name
        if Util.is_root():
            subprocess.run(f"rm -Rf {path}", shell=True)
        else:
            error(f"You're not root! You must manually delete installed OSes at {path}")


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
        Settings.add_machine(new_mach)
        fatal(f"Machine is not configured in Mama! Added as new machine: {mac}")

    if mach.os == "NONE/NONE":
        fatal("#!pxe\necho Skipping ipxe. Using next boot option.\nexit 1")

    # Extract clean IP and port
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
        attr = Util.get_line(
            "{os | kernel | resources | params | vmparams | startcmd | stopcmd | powercmd | start-timeout}: "
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


def cmd_get(machine: Union[str, bool, None] = False, attr: Optional[str] = None) -> None:
    mach = select_machine(machine)

    if not isinstance(mach, Machine):
        return

    if not attr:
        attr = Util.get_line(
            "{os | params | resources | kernel | reservation | ip | mac | vmparams | startcmd | stopcmd | powercmd | power}: "
        ).strip()

    out(str(mach.get(attr)))


def setup_cli_parser() -> argparse.ArgumentParser:
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parent_parser.add_argument("--debug-lock", dest="debug_lock", action="store_true", help="Enable lock debugging")
    parent_parser.add_argument("--debug-pid", dest="debug_pid", action="store_true", help="Enable PID debugging")
    parent_parser.add_argument("--args", type=str, default="", help="Additional arguments to pass along")
    parent_parser.add_argument("--packages", type=str, default="", help="Additional packages to install")
    parent_parser.add_argument("--no-ssh-timeout", dest="no_ssh_timeout", action="store_true", help="Allows SSH commands to run indefinitely")
    parent_parser.add_argument("--no-retry", dest="no_retry", action="store_true", help="Don't retry starting machines multiple times")
    parent_parser.add_argument("--no-overwrite", dest="no_overwrite", action="store_true", help="Skip overwriting existing OS on copy")

    root_parser = argparse.ArgumentParser(prog="mama", description="MAMA Machine and OS Management Framework", parents=[parent_parser])
    subparsers = root_parser.add_subparsers(dest="command", help="Available commands")

    subparsers.add_parser("list", parents=[parent_parser], help="List configured machines")

    p_list_os = subparsers.add_parser("list-os", parents=[parent_parser], help="List available OSes")
    p_list_os.add_argument("machine", nargs="?", default=False, help="Target machine")

    p_list_kernel = subparsers.add_parser("list-kernel", parents=[parent_parser], help="List available kernels")
    p_list_kernel.add_argument("machine", help="Target machine")

    subparsers.add_parser("list-resources", parents=[parent_parser], help="List all resources")

    p_log = subparsers.add_parser("log", parents=[parent_parser], help="Print log file")
    p_log.add_argument("machine", nargs="?", default=False, help="Target machine")

    p_tail = subparsers.add_parser("tail", parents=[parent_parser], help="Tail log file")
    p_tail.add_argument("machine", nargs="?", default=False, help="Target machine")

    p_clear_log = subparsers.add_parser("clear-log", parents=[parent_parser], help="Clear log for machine")
    p_clear_log.add_argument("machine", nargs="?", default=False, help="Target machine")

    # Job subcommands
    job_p = subparsers.add_parser("job", parents=[parent_parser], help="Execute job workflows")
    job_p.add_argument("job_name", help="Job name")
    job_sub = job_p.add_subparsers(dest="job_action", help="Job action")

    prep_p = job_sub.add_parser("prepare", parents=[parent_parser], help="Execute prepare job")
    prep_p.add_argument("arch", help="Target architecture")
    prep_p.add_argument("os", help="Target OS")
    prep_p.add_argument("worker", nargs="?", default="jobs", help="Worker machine name")

    run_p = job_sub.add_parser("run", parents=[parent_parser], help="Execute run job")
    run_p.add_argument("machine", help="Target machine")
    run_p.add_argument("arch", help="Target architecture")
    run_p.add_argument("os", help="Target OS")

    # OS Build and Management
    p_build = subparsers.add_parser("build-os", parents=[parent_parser], help="Build a deployable OS")
    p_build.add_argument("arch", help="Architecture")
    p_build.add_argument("os", help="OS name")
    p_build.add_argument("builder", nargs="?", default="os-builder", help="Builder machine name")

    p_run_script = subparsers.add_parser("run-os-build-script", parents=[parent_parser], help="Run local OS build script")
    p_run_script.add_argument("arch", help="Architecture")
    p_run_script.add_argument("os", help="OS name")

    p_install = subparsers.add_parser("install-os", parents=[parent_parser], help="Install OS to a machine")
    p_install.add_argument("machine", help="Target machine")
    p_install.add_argument("arch", help="Architecture")
    p_install.add_argument("os", help="Source OS name")
    p_install.add_argument("name", nargs="?", default=None, help="Destination installation name")

    p_del_os = subparsers.add_parser("delete-os", parents=[parent_parser], help="Delete an OS installed on a machine")
    p_del_os.add_argument("machine", help="Target machine")
    p_del_os.add_argument("arch", help="Architecture")
    p_del_os.add_argument("os", help="OS name")

    p_copy_os = subparsers.add_parser("copy-os", parents=[parent_parser], help="Copy OS from one machine to another")
    p_copy_os.add_argument("src_mach", help="Source machine")
    p_copy_os.add_argument("arch", help="Architecture")
    p_copy_os.add_argument("os", help="OS name")
    p_copy_os.add_argument("dst_mach", help="Destination machine")

    # Machine operations
    subparsers.add_parser("new", parents=[parent_parser], help="Add a new machine")

    p_del = subparsers.add_parser("delete", parents=[parent_parser], help="Delete an existing machine")
    p_del.add_argument("machine", nargs="?", default=False, help="Machine to delete")

    p_info = subparsers.add_parser("info", parents=[parent_parser], help="Show detailed machine info")
    p_info.add_argument("machine", nargs="?", default=False, help="Target machine")

    p_start = subparsers.add_parser("start", parents=[parent_parser], help="Boot machine")
    p_start.add_argument("machine", nargs="?", default=False, help="Target machine")

    p_vm = subparsers.add_parser("start-vm", parents=[parent_parser], help="Boot virtual instance of machine")
    p_vm.add_argument("machine", nargs="?", default=False, help="Target machine")

    p_stop = subparsers.add_parser("stop", parents=[parent_parser], help="Stop machine")
    p_stop.add_argument("machine", nargs="?", default=False, help="Target machine")

    p_clear = subparsers.add_parser("clear", parents=[parent_parser], help="Stop machine and clear state")
    p_clear.add_argument("machine", nargs="?", default=False, help="Target machine or 'all'")

    p_conn = subparsers.add_parser("connect", parents=[parent_parser], help="SSH connect to machine")
    p_conn.add_argument("machine", nargs="?", default=False, help="Target machine")

    p_set = subparsers.add_parser("set", parents=[parent_parser], help="Set machine attributes")
    p_set.add_argument("machine", nargs="?", default=False, help="Target machine")
    p_set.add_argument("attr", nargs="?", default=None, help="Attribute name")
    p_set.add_argument("val", nargs="*", default=[], help="Attribute values")

    p_get = subparsers.add_parser("get", parents=[parent_parser], help="Get machine attribute")
    p_get.add_argument("machine", nargs="?", default=False, help="Target machine")
    p_get.add_argument("attr", nargs="?", default=None, help="Attribute name")

    p_res = subparsers.add_parser("reserve", parents=[parent_parser], help="Reserve machine")
    p_res.add_argument("machine", nargs="?", default=False, help="Target machine")

    p_rel = subparsers.add_parser("release", parents=[parent_parser], help="Release machine reservation")
    p_rel.add_argument("machine", nargs="?", default=False, help="Target machine")

    p_rel_f = subparsers.add_parser("release-forced", parents=[parent_parser], help="Force release machine reservation")
    p_rel_f.add_argument("machine", nargs="?", default=False, help="Target machine")

    p_wait = subparsers.add_parser("wait", parents=[parent_parser], help="Wait for machine availability")
    p_wait.add_argument("machine", nargs="?", default=False, help="Target machine")

    subparsers.add_parser("serve", parents=[parent_parser], help="Start iPXE boot server")

    p_ipxe = subparsers.add_parser("ipxe", parents=[parent_parser], help="Generate iPXE boot script")
    p_ipxe.add_argument("mac", help="Machine MAC address")
    p_ipxe.add_argument("server_ip", help="Server IP address")
    p_ipxe.add_argument("port", nargs="?", default=None, help="Server port (optional)")

    p_bootinfo = subparsers.add_parser("bootinfo", parents=[parent_parser], help="Output kernel, initrd, and append params")
    p_bootinfo.add_argument("mac", help="Machine MAC address")
    p_bootinfo.add_argument("server_ip", help="Server IP address")
    p_bootinfo.add_argument("port", nargs="?", default=None, help="Server port (optional)")

    return root_parser


def main() -> None:
    if len(sys.argv) <= 1:
        print_usage(sys.argv)
        sys.exit(0)

    parser = setup_cli_parser()
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

    if Settings.load() is False:
        fatal(f"Failed to open settings file: {config.SETTINGS_FILE}\n")

    cmd = parsed_args.command

    if cmd == "list":
        cmd_list()
    elif cmd == "list-os":
        cmd_list_os(parsed_args.machine)
    elif cmd == "list-kernel":
        cmd_list_kernel(parsed_args.machine)
    elif cmd == "list-resources":
        cmd_list_resources()
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
    else:
        print_usage(sys.argv)
        sys.exit(0)


if __name__ == "__main__":
    main()