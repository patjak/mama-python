#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import sys

# Ensure MAMA installation directory is in sys.path
script_dir = pathlib.Path(__file__).parent.resolve()
sys.path.insert(0, str(script_dir))

import builder
import cli_commands
from cli_parser import HAS_ARGCOMPLETE, setup_cli_parser
import config
from repository import MachineRepository


def main() -> None:
    parser = setup_cli_parser()

    if HAS_ARGCOMPLETE:
        import argcomplete
        argcomplete.autocomplete(parser)

    args = parser.parse_args()

    # Configure global debug flags
    if getattr(args, "debug", False):
        config.DEBUG = True
    if getattr(args, "debug_lock", False):
        config.DEBUG_LOCK = True
    if getattr(args, "debug_pid", False):
        config.DEBUG_PID = True

    if not args.command:
        parser.print_help()
        sys.exit(0)

    # Initialize machine repository data
    try:
        MachineRepository.load()
    except Exception:
        pass

    # Command Dispatch Routing
    if args.command == "list":
        cli_commands.cmd_list(
            item=args.item,
            target=args.target,
            fast=args.fast,
        )

    elif args.command == "queue":
        cli_commands.cmd_queue(arg=args.machine)

    elif args.command == "log":
        cli_commands.cmd_log(
            arg=args.machine,
            vm=args.vm,
            follow=args.follow,
            clear=args.clear,
        )

    elif args.command == "history":
        cli_commands.cmd_history(arg=args.machine)

    elif args.command == "job":
        if args.job_action == "prepare":
            cli_commands.cmd_job_prepare(
                job_name=args.job_name,
                arch=args.arch,
                os_name=args.os,
                worker=args.worker,
                args=args.args,
                no_ssh_timeout=args.no_ssh_timeout,
            )
        elif args.job_action == "run":
            cli_commands.cmd_job_run(
                job_name=args.job_name,
                machine=args.machine,
                arch=args.arch,
                os_name=args.os,
                args=args.args,
                no_ssh_timeout=args.no_ssh_timeout,
                no_retry=args.no_retry,
            )
        else:
            parser.parse_args(["job", "--help"])

    elif args.command == "build-os":
        builder.build_os(
            arch=args.arch,
            os_name=args.os,
            builder=args.builder,
            packages=args.packages,
        )

    elif args.command == "run-os-build-script":
        builder.run_os_build_script(
            arch=args.arch,
            os_name=args.os,
            packages=args.packages,
        )

    elif args.command == "install-os":
        cli_commands.cmd_install_os(
            machine=args.machine,
            arch=args.arch,
            os_name=args.os,
            name=args.name,
        )

    elif args.command == "delete-os":
        cli_commands.cmd_delete_os(
            machine=args.machine,
            arch=args.arch,
            os_name=args.os,
        )

    elif args.command == "copy-os":
        cli_commands.cmd_copy_os(
            src_machine_name=args.src_mach,
            arch=args.arch,
            os_name=args.os,
            dst_machine_name=args.dst_mach,
            no_overwrite=args.no_overwrite,
        )

    elif args.command == "new":
        cli_commands.cmd_new()

    elif args.command == "delete":
        cli_commands.cmd_delete(arg=args.machine)

    elif args.command == "info":
        cli_commands.cmd_info(arg=args.machine)

    elif args.command == "start":
        cli_commands.cmd_start(arg=args.machine)

    elif args.command == "start-vm":
        cli_commands.cmd_start_vm(arg=args.machine)

    elif args.command == "stop":
        cli_commands.cmd_stop(arg=args.machine)

    elif args.command == "clear":
        cli_commands.cmd_clear(arg=args.machine)

    elif args.command == "connect":
        cli_commands.cmd_connect(arg=args.machine)

    elif args.command == "set":
        cli_commands.cmd_set(
            machine=args.machine,
            attr=args.attr,
            val_list=args.val,
        )

    elif args.command == "get":
        cli_commands.cmd_get(
            machine=args.machine,
            attr=args.attr,
        )

    elif args.command == "reserve":
        cli_commands.cmd_reserve(arg=args.machine)

    elif args.command == "release":
        cli_commands.cmd_release(
            arg=args.machine,
            force=args.force,
        )

    elif args.command == "wait":
        cli_commands.cmd_wait(
            arg=args.machine,
            reason=args.reason,
        )

    elif args.command == "serve":
        cli_commands.cmd_serve()

    elif args.command == "ipxe":
        cli_commands.cmd_ipxe(
            mac=args.mac,
            server_ip=args.server_ip,
            port=args.port,
        )

    elif args.command == "bootinfo":
        cli_commands.cmd_bootinfo(
            mac=args.mac,
            server_ip=args.server_ip,
            port=args.port,
        )

    elif args.command == "dhcp":
        cli_commands.cmd_dhcp(dhcp_action=args.dhcp_action)

    elif args.command == "nfs":
        cli_commands.cmd_nfs(nfs_action=args.nfs_action)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)