# Installing MAMA on openSUSE Leap 16.0

This guide walks through installing and configuring **MAMA (Machine Administration & Management Architecture)** on a clean installation of **openSUSE Leap 16.0**.

---

## Architecture Overview

MAMA relies on the following core services to provide network booting, state tracking, and storage management:

| Component | Service / Technology | Purpose |
|---|---|---|
| **CLI & Runtime** | Python CLI (`rich`, `questionary`) | Management, automation, and interactive commands |
| **HTTP Boot Server** | `mama-boot.service` (port 8080) | Serves dynamic iPXE scripts, kernels, and initrds |
| **DHCP & TFTP** | `dnsmasq` (`/etc/dnsmasq.d/mama.conf`) | Managed DHCP IP leases and TFTP firmware delivery |
| **Database** | SQLite (`/usr/share/mama/mama.db`) | State tracking, machine inventory, queues, and history |
| **Root Filesystem** | NFSv4 (`nfs-kernel-server`) | Shared root filesystems for booted targets |
| **Virtualization** | QEMU / KVM (`qemu-x86`, `qemu-arm`) | Local virtual test machines |

---

## 1. Install System Packages

Install the required host system packages, Python runtime tools, QEMU emulators, and bootloader binaries via `zypper`:

```bash
sudo zypper refresh
sudo zypper install -y \
    git \
    python3 \
    python3-pip \
    python3-setuptools \
    dnsmasq \
    nfs-kernel-server \
    ipxe-bootimgs \
    qemu-x86 \
    qemu-arm \
    qemu-kvm \
    NetworkManager \
    firewalld
```

*(Optional)* If you plan to build OS root filesystems with Kiwi on the host:
```bash
# Kiwi image builder (required for 'mama build-os')
sudo zypper install -y python3-kiwi

# Package manager for building Fedora appliances (optional)
sudo zypper install -y dnf5
```

---

## 2. Configure Network Bridge (`br0`)

MAMA requires a dedicated network bridge (`br0`) with a static IPv4 address (default: `192.168.2.1/24`) attached to the physical interface connected to your target test machines.

### Step 2.1: Find Your Physical Interface
Run `nmcli device` to find your target network interface (e.g., `ens19`, `eth1`):
```bash
nmcli device
```

### Step 2.2: Create and Activate the Bridge
Replace `[INTERFACE-NAME]` with your physical interface name:

```bash
# 1. Create the bridge interface
sudo nmcli connection add type bridge con-name br0 ifname br0

# 2. Attach physical interface as a bridge slave
sudo nmcli connection add type bridge-slave ifname [INTERFACE-NAME] master br0

# 3. Assign static IP address (192.168.2.1/24)
sudo nmcli connection modify br0 ipv4.addresses 192.168.2.1/24
sudo nmcli connection modify br0 ipv4.method manual

# 4. Bring up the bridge
sudo nmcli connection up br0
```

Verify that `br0` is up and assigned `192.168.2.1`:
```bash
ip addr show br0
```

---

## 3. Firewall, NAT & IP Forwarding

Target machines on the `192.168.2.0/24` subnet require access to DHCP, TFTP, HTTP, and NFS on the MAMA host, as well as internet access via NAT masquerading for package downloads.

### Step 3.1: Configure Firewalld
Assign `br0` to the `trusted` zone and enable NAT masquerading:

```bash
# Assign bridge to trusted zone
sudo firewall-cmd --permanent --zone=trusted --change-interface=br0

# Enable NAT masquerading
sudo firewall-cmd --permanent --zone=public --add-masquerade
sudo firewall-cmd --permanent --zone=trusted --add-masquerade

# Reload firewall rules
sudo firewall-cmd --reload
```

### Step 3.2: Enable IPv4 Kernel Forwarding
Enable packet forwarding across interfaces:

```bash
echo "net.ipv4.ip_forward = 1" | sudo tee /etc/sysctl.d/99-mama.conf
sudo sysctl -p /etc/sysctl.d/99-mama.conf
```

---

## 4. Install MAMA

### Step 4.1: Clone the Repository
Clone MAMA to your desired location (e.g. `/usr/share/mama` or your local development directory):

```bash
git clone https://github.com/patjak/mama-python.git /usr/share/mama
cd /usr/share/mama
```

### Step 4.2: Install Python Dependencies
Install runtime Python dependencies using `pip`:

```bash
pip install -e ".[cli]"
```

### Step 4.3: Run the Automated Installer
Execute `install.sh` as root:

```bash
sudo ./install.sh
```

The installer automatically handles all MAMA environment and service setup:
1. **System Group & Users**: Creates the `mama` system group and adds your user account to `mama` and `kvm`.
2. **Directory Hierarchy**: Sets up `/usr/share/mama/{machines,os,os-descriptions,jobs,tftp,log,locks}`.
3. **Permissions**: Applies targeted non-recursive permissions (`chmod 2775`, `chgrp mama`) to management directories and SQLite database files.
4. **QEMU Bridge Helper**: Configures `/etc/qemu/bridge.conf` allowing unprivileged VM attachment to `br0`.
5. **Configuration**: Creates default configuration in `/etc/mama/mama.conf`.
6. **TFTP Bootloaders**: Stages vendor iPXE binaries (`ipxe.efi`, `undionly.kpxe`, `ipxe-arm64.efi`) into the TFTP root.
7. **NFS Exports**: Configures `/etc/exports.d/mama.exports` and reloads `nfs-server`.
8. **SSH Keys**: Stages your developer SSH public key into `/usr/share/mama/authorized_keys`.
9. **Default Worker Machines**: Automatically registers default 'jobs' and 'os-builder' virtual machines in SQLite.
10. **Boot Service**: Installs, enables, and starts the `mama-boot.service` HTTP server.
11. **CLI Links**: Symlinks the `mama` executable to `/usr/local/bin/mama`.

> **Activate Group Membership:** If your user account was just added to the `mama` and `kvm` groups, activate them in your current shell:
> ```bash
> newgrp mama
> newgrp kvm
> ```
> (or log out and back in). Members of `mama` and `kvm` can start, monitor, and manage machines and VMs without `sudo`.

---

## 5. Synchronize DHCP Server (`dnsmasq`)

Once MAMA is installed, synchronize the `dnsmasq` configuration and start the DHCP service:

```bash
sudo mama dhcp sync
```

> **Jumbo Frames / MTU:** If your network runs Jumbo frames, you can pass `--mtu <value>` (e.g. `sudo mama dhcp sync --mtu 9000`) or set `dhcp_mtu = 9000` in `/etc/mama/mama.conf`.

Enable and start `dnsmasq` on boot:
```bash
sudo systemctl enable --now dnsmasq
```

---

## 6. Verification

### Step 6.1: Verify Background Services
Ensure all three background services are active and running:
```bash
systemctl is-active dnsmasq nfs-server mama-boot
```
All three should report `active`.

### Step 6.2: Test CLI as Non-Root User
Run `mama list` as your normal user (without `sudo`):
```bash
mama list
```

You should see the formatted machine table:
```text
                                          Configured Machines                                          
┏━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━┳━━━━┳━━━━━┳━━━━━┳━━━━━━━┳━━━━━┓
┃ No. ┃ Name       ┃ State   ┃ On  ┃ Up ┃ Pwr ┃ VM  ┃ Queue ┃ Job ┃
┡━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━╇━━━━╇━━━━━╇━━━━━╇━━━━━━━╇━━━━━┩
│   1 │ jobs       │ offline │  -  │  - │   - │ Yes │     0 │ -   │
│   2 │ os-builder │ offline │  -  │  - │   - │ Yes │     0 │ -   │
└─────┴────────────┴─────────┴─────┴────┴─────┴─────┴───────┴─────┘
```

---

## Permissions & Security Architecture

### Why Targeted Non-Recursive Permissions?
MAMA operates with non-root privileges for routine machine operations, reservations, queue processing, and log inspection.

- **Group `mama`**: Has write access to the SQLite database (`mama.db`), machine locks (`locks/`), and run logs (`log/`).
- **Group `kvm`**: Has access to `/dev/kvm` and the QEMU bridge helper, allowing virtual machines to attach directly to `br0` without `sudo`.
- **Rootfs Protection**: The subdirectories `/usr/share/mama/machines/` and `/usr/share/mama/os/` contain complete guest Linux root filesystems. Permissions are applied **non-recursively** so guest system files (`root:root`, `/etc/shadow`, setuid binaries) retain their required ownership and permissions.

---

## Troubleshooting

- **Permissions error accessing logs (`[Errno 13] Permission denied`)**:
  Ensure your user is in the `mama` group (`groups`) and permissions on `/usr/share/mama/log` are set:
  ```bash
  sudo chgrp -R mama /usr/share/mama/log
  sudo chmod 2775 /usr/share/mama/log
  sudo chmod 0664 /usr/share/mama/log/*
  ```

- **Target machine hangs at DHCP / PXE**:
  Inspect dnsmasq DHCP requests in the journal:
  ```bash
  journalctl -u dnsmasq -f
  ```

- **iPXE boot script errors**:
  Check the `mama-boot` HTTP server logs:
  ```bash
  journalctl -u mama-boot.service -f
  ```

- **NFS mount timeouts on target boot**:
  Confirm that `br0` is in the firewalld `trusted` zone and check exported paths:
  ```bash
  sudo exportfs -v
  sudo firewall-cmd --get-active-zones
  ```
