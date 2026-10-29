# Installing MAMA on openSUSE Leap 16.0

This guide walks through installing and configuring **MAMA (Machine Administration & Management Architecture)** on a clean installation of **openSUSE Leap 16.0**.

---

## Architecture Overview

Modern MAMA has been completely rewritten in Python 3. It replaces the legacy PHP/Apache/ISC-DHCP stack with lightweight, native services:

| Component | Legacy MAMA | Modern MAMA (Python 3) | Purpose |
|---|---|---|---|
| **Runtime** | PHP 8 (`php-cli`, `apache2-mod_php8`) | Python 3 (`python3-setuptools`, `rich`, `questionary`) | CLI management & automation |
| **HTTP Server** | Apache 2 (`/srv/www/htdocs/mama`) | Built-in Python HTTP server (`mama-boot.service`) | Serves iPXE scripts, kernels, initrd |
| **DHCP & TFTP** | ISC `dhcp-server` + `atftpd` | `dnsmasq` (`/etc/dnsmasq.d/mama.conf`) | DHCP leases & TFTP firmware delivery |
| **Database** | XML (`mama.xml`) | SQLite (`/usr/share/mama/mama.db`) | State, inventory, queues, and leases |
| **Root Filesystem** | NFSv4 (`nfs-kernel-server`) | NFSv4 (`nfs-kernel-server`) | Root filesystems for booted targets |
| **Virtualization** | QEMU (`qemu-x86`, `qemu-arm`) | QEMU / KVM (`qemu-x86`, `qemu-arm`) | Virtual machine test targets |

---

## 1. Install System Packages

Install the required system services, Python tools, QEMU emulators, and boot binaries via `zypper`:

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
sudo zypper install -y python3-kiwi
```

---

## 2. Configure Network Bridge (`br0`)

MAMA requires a dedicated network bridge (`br0`) with a static IPv4 address (default: `192.168.2.1/24`) attached to the physical interface connected to your target test machines.

### Step 2.1: Find Your Physical Interface
Run `ip link` or `nmcli device` to find your target network card (e.g., `ens19`, `eth1`):
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
Assign `br0` to the `trusted` zone (or open ports) and enable masquerading:

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

## 4. Storage & NFS Server Configuration

MAMA exports `/usr/share/mama` over NFSv4 so booted target machines and remote builders can mount their root filesystems.

### Step 4.1: Create Directory Structure
```bash
sudo mkdir -p /usr/share/mama/{machines,os,os-descriptions,jobs,tftp,log}
```

### Step 4.2: Configure NFS Export
Create an export drop-in under `/etc/exports.d/`:

```bash
sudo mkdir -p /etc/exports.d
echo '/usr/share/mama 192.168.2.0/24(rw,async,no_root_squash,no_subtree_check,fsid=0)' | sudo tee /etc/exports.d/mama.exports
```

### Step 4.3: Enable and Start NFS Service
```bash
sudo systemctl enable --now nfs-server
```

Verify export:
```bash
sudo exportfs -v
```

---

## 5. TFTP & iPXE Boot Binaries

Modern MAMA uses `dnsmasq`'s built-in TFTP server serving files directly out of `/usr/share/mama/tftp`.

Copy the vendor iPXE binaries provided by the `ipxe-bootimgs` package into the TFTP directory:

```bash
sudo mkdir -p /usr/share/mama/tftp

# x86_64 UEFI firmware bootloader
sudo cp /usr/share/ipxe/ipxe-x86_64.efi /usr/share/mama/tftp/ipxe.efi

# Legacy BIOS PXE bootloader
sudo cp /usr/share/ipxe/undionly.kpxe /usr/share/mama/tftp/undionly.kpxe

# ARM64 UEFI firmware bootloader
sudo cp /usr/share/ipxe/snp-arm64.efi /usr/share/mama/tftp/ipxe-arm64.efi
```

---

## 6. QEMU Bridge Helper Configuration

To allow virtual machines (QEMU/KVM) to attach directly to `br0`:

```bash
sudo mkdir -p /etc/qemu
echo "allow br0" | sudo tee -a /etc/qemu/bridge.conf
```

---

## 7. Install MAMA

### Step 7.1: Clone the Repository
Clone MAMA to your desired location (e.g. `/usr/share/mama` or your local development directory):

```bash
# Clone repository
git clone https://github.com/patjak/mama-python.git /usr/share/mama
cd /usr/share/mama
```

### Step 7.2: Install Python Dependencies
Install runtime Python dependencies using `pip` (or system packages):

```bash
pip install -e ".[cli]"
```

### Step 7.3: Run the Installer
Run the installer script as root:

```bash
sudo ./install.sh
```

The installer will:
1. Create the `mama` system group.
2. Add the invoking user (`$SUDO_USER`) to the `mama` group.
3. Configure non-recursive permissions (`chmod 2775 /usr/share/mama /usr/share/mama/log`).
4. Install and enable the `mama-boot.service` systemd unit.
5. Create symlinks for `/usr/bin/mama` and `/usr/local/bin/mama`.

> **Important**: If your user account was just added to the `mama` group, activate the new group in your current shell:
> ```bash
> newgrp mama
> ```
> (or log out and back in).

---

## 8. Configure DHCP & DNS (dnsmasq)

Modern MAMA automatically generates `/etc/dnsmasq.d/mama.conf` and the TFTP boot script (`autoexec.ipxe`).

### Step 8.1: Synchronize dnsmasq Configuration
Run the synchronization command:

```bash
sudo mama dhcp sync
```

This will:
- Write `/etc/dnsmasq.d/mama.conf` configured for bridge `br0` (DHCP range `192.168.2.100` – `192.168.2.200`).
- Generate `/usr/share/mama/tftp/autoexec.ipxe` pointing to the MAMA boot server (`http://192.168.2.1:8080/boot.py?mac=${mac}`).
- Automatically reload or restart the `dnsmasq` service.

### Step 8.2: Enable dnsmasq on Boot
```bash
sudo systemctl enable --now dnsmasq
```

---

## 9. Enable MAMA Boot Server

The `mama-boot` systemd service runs the internal HTTP server on port 8080, serving dynamic iPXE scripts and kernel/initrd boot images:

```bash
sudo systemctl enable --now mama-boot.service
```

Verify service status:
```bash
systemctl status mama-boot.service
```

---

## 10. Configure SSH Access

Generate an SSH key pair (if you don't already have one) and place the public key in `/usr/share/mama/authorized_keys`. This key is automatically injected into booted machine root filesystems to allow MAMA to execute commands over SSH:

```bash
# Generate SSH key if not already present
[ -f ~/.ssh/id_ed25519 ] || ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519

# Copy public key to MAMA authorized_keys
cp ~/.ssh/id_ed25519.pub /usr/share/mama/authorized_keys
chmod 0644 /usr/share/mama/authorized_keys
```

---

## 11. Verification

### Step 11.1: Verify Service Status
Ensure all three background services are active and running:
```bash
systemctl is-active dnsmasq nfs-server mama-boot
```
All three should report `active`.

### Step 11.2: Check Machine Listing
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

## 12. Troubleshooting

- **Permissions error accessing logs (`[Errno 13] Permission denied`)**:
  Ensure permissions on the log directory are set correctly:
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
