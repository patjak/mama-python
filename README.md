# MAMA (Machine Administration & Management Architecture)

MAMA is an automated provisioning, network booting (iPXE/NFS), and machine management framework for bare-metal and virtual (QEMU/KVM) test machines.

MAMA provides a centralized CLI and background services to manage bare-metal devices and virtual machines, schedule automated test jobs, dynamically provision root filesystems over NFS, and monitor boot cycles.

---

## Key Features

- **Dynamic Network Booting:** Built-in HTTP server (`mama-boot.service`) generates machine-specific iPXE configurations on the fly and delivers kernels and initrds.
- **Integrated DHCP & TFTP:** Automatically configures and syncs `dnsmasq` (`mama dhcp sync`) for static IP leases and TFTP firmware delivery.
- **NFSv4 Root Filesystems:** Shares extracted guest OS root filesystems directly over NFSv4, avoiding disk flashing for rapid test iterations.
- **Real-Time Lifecycle Tracking:** Live status monitoring across provisioning, network booting, and online operational states.
- **Adaptive Boot Timeouts:** Records rolling boot history in SQLite to calculate dynamic, target-specific boot timeouts.
- **Automated Jobs & Queues:** Built-in job engine supporting automated preparation and execution phases with per-machine FIFO queues and configurable timeouts.
- **Multi-User Permissions:** Targeted group-level access via the `mama` system group allows developers to manage machines and view logs without `sudo`.

---

## Jobs

MAMA includes an automated job runner designed for executing test suites (such as IGT GPU Tools), OS building tasks, and benchmark scripts across configured machines:

- **Two-Phase Workflow:** Jobs support separate `prepare` (building artifacts or setting up prerequisites, typically on a dedicated builder or worker machine) and `run` phases (executing test commands on the target hardware).
- **Queuing & Concurrency:** Each target machine features an active FIFO job queue. Jobs are queued and dispatched sequentially, with live queue depth reported in machine listings.
- **Configurable Timeouts:** Jobs can be assigned explicit execution timeouts (`--timeout <mins>`) that only begin counting once the target machine is booted and reachable via SSH.
- **Stale Job Detection:** MAMA tracks active job process IDs and automatically flags and clears stale jobs if a worker process terminates unexpectedly.

---

## Directory Layout

All operational assets, target root filesystems, and state databases reside under `/usr/share/mama`:

```text
/usr/share/mama/
├── mama.db                 # SQLite inventory, job queues & boot history (root:mama, 0664)
├── log/                    # Central and machine-specific execution logs (root:mama, 2775)
│   ├── mama-log            # Central operational log
│   ├── <machine>           # Machine boot and command logs
│   └── <machine>-vmlog     # Serial console output for VMs
├── machines/               # Target machine root filesystems (root:root, 0755)
│   └── <name>/<arch>/<os>/ # Extracted Linux NFS rootfs (preserves guest permissions)
├── os/                     # Master OS rootfs templates built by Kiwi-ng (root:root, 0755)
├── os-descriptions/        # Kiwi XML appliance descriptions & configs
├── jobs/                   # Automated job definitions (e.g. igt, os-builder)
├── tftp/                   # dnsmasq TFTP root (autoexec.ipxe, firmware binaries)
└── authorized_keys         # SSH public keys injected into booted machines
```

---

## Installation

For detailed step-by-step installation and configuration instructions on openSUSE Leap 16.0, please refer to [INSTALL.md](INSTALL.md).

---

## License

This project is licensed under the GNU General Public License v2.0 - see the [LICENSE](LICENSE) file for details.
