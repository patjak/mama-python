from __future__ import annotations

import logging
import sys

from cli_commands import (
    cmd_bootinfo,
    cmd_clear,
    cmd_connect,
    cmd_copy_os,
    cmd_delete,
    cmd_delete_os,
    cmd_dhcp,
    cmd_get,
    cmd_history,
    cmd_info,
    cmd_install_os,
    cmd_ipxe,
    cmd_job_prepare,
    cmd_job_run,
    cmd_list,
    cmd_list_kernel,
    cmd_list_os,
    cmd_list_resources,
    cmd_log,
    cmd_new,
    cmd_queue,
    cmd_release,
    cmd_reserve,
    cmd_serve,
    cmd_set,
    cmd_start,
    cmd_start_vm,
    cmd_stop,
    cmd_wait,
)
from cli_parser import HAS_ARGCOMPLETE, setup_cli_parser
import config
from logger import Log, fatal
from repository import MachineRepository


def main() -> None:
    parser = setup_cli_parser()

    if HAS_ARGCOMPLETE:
        import argcomplete
        argcomplete.autocomplete(parser)

    if len(sys.argv) <= 1:
        parser.print_help()
        sys.exit(0)

    parsed_args = parser.parse_args()

    config.DEBUG = parsed_args.debug
    config.DEBUG_LOCK = parsed_args.debug_lock
    config.DEBUG_PID = parsed_args.debug_pid

    if parsed_args.debug:
        Log.set_level(logging.DEBUG)

    db_path = getattr(config, "DB_FILE", f"{getattr(config, 'MAMA_PATH', '/usr/share/mama')}/mama.db")
    if MachineRepository.load() is False:
        fatal(f"Failed to initialize database: {db_path}\n")

    cmd = parsed_args.command

    if cmd == "list":
        cmd_list(fast=getattr(parsed_args, "fast", False))
    elif cmd == "queue":
        cmd_queue(parsed_args.machine)
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
            cmd_job_prepare(
                parsed_args.job_name,
                parsed_args.arch,
                parsed_args.os,
                parsed_args.worker,
                args=parsed_args.args or "",
                no_ssh_timeout=parsed_args.no_ssh_timeout,
            )
        elif parsed_args.job_action == "run":
            cmd_job_run(
                parsed_args.job_name,
                parsed_args.machine,
                parsed_args.arch,
                parsed_args.os,
                args=parsed_args.args or "",
                no_ssh_timeout=parsed_args.no_ssh_timeout,
                no_retry=parsed_args.no_retry,
            )
        else:
            fatal("Invalid job command")
    elif cmd == "build-os":
        from builder import build_os
        build_os(parsed_args.arch, parsed_args.os, parsed_args.builder, packages=parsed_args.packages or "")
    elif cmd == "run-os-build-script":
        from builder import run_os_build_script
        run_os_build_script(parsed_args.arch, parsed_args.os, packages=parsed_args.packages or "")
    elif cmd == "install-os":
        cmd_install_os(parsed_args.machine, parsed_args.arch, parsed_args.os, parsed_args.name)
    elif cmd == "delete-os":
        cmd_delete_os(parsed_args.machine, parsed_args.arch, parsed_args.os)
    elif cmd == "copy-os":
        cmd_copy_os(
            parsed_args.src_mach,
            parsed_args.arch,
            parsed_args.os,
            parsed_args.dst_mach,
            no_overwrite=parsed_args.no_overwrite,
        )
    elif cmd == "new":
        cmd_new()
    elif cmd == "delete":
        cmd_delete(parsed_args.machine)
    elif cmd == "info":
        cmd_info(parsed_args.machine)
    elif cmd == "log":
        cmd_log(
            parsed_args.machine,
            vm=parsed_args.vm,
            follow=parsed_args.follow,
            clear=parsed_args.clear,
        )
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
        cmd_release(parsed_args.machine, force=parsed_args.force)
    elif cmd == "wait":
        cmd_wait(parsed_args.machine)
    elif cmd == "serve":
        cmd_serve()
    elif cmd == "ipxe":
        cmd_ipxe(parsed_args.mac, parsed_args.server_ip, parsed_args.port)
    elif cmd == "bootinfo":
        cmd_bootinfo(parsed_args.mac, parsed_args.server_ip, parsed_args.port)
    elif cmd == "dhcp":
        cmd_dhcp(parsed_args.dhcp_action)
    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()