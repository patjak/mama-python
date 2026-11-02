from __future__ import annotations

import grp
import os
import pathlib
import re
import urllib.error
import urllib.request
import xml.dom.minidom
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Set, Tuple

from . import config

DEFAULT_ROOT_PASSWORD_HASH = "$1$wYJUgpM5$RXMMeASDc035eX.NbYWFl0"

FALLBACK_LEAP_VERSIONS = ["16.1", "16.0", "15.6", "15.5"]
FEDORA_VERSIONS = ["44", "43", "42", "41", "rawhide"]


def check_url_exists(url: str, timeout: float = 2.0) -> bool:
    """Check if a remote URL exists and returns HTTP 200."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MAMA/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def probe_opensuse_leap_versions(timeout: float = 3.0) -> List[str]:
    """Probe download.opensuse.org/distribution/leap/ for available Leap releases."""
    url = "http://download.opensuse.org/distribution/leap/"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MAMA/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
            # Look for href="./15.6/" or href="15.6/" directory entries
            matches = re.findall(r'href="(?:\./)?([1-9][0-9]\.[0-9]+)/?"', html)
            if matches:
                # Deduplicate and sort numerically descending (newest first)
                unique_versions = sorted(
                    set(matches),
                    key=lambda v: [int(x) for x in v.split(".")],
                    reverse=True,
                )
                return [v for v in unique_versions if v.startswith(("15.", "16."))]
    except Exception:
        pass
    return FALLBACK_LEAP_VERSIONS


def get_default_kernel_repo(distro_family: str, version: str) -> str:
    """Return the canonical KOTD repository URL for a given release."""
    clean_ver = version.strip().lower()
    if distro_family == "opensuse":
        if clean_ver == "tumbleweed":
            return "https://download.opensuse.org/repositories/Kernel:/HEAD/standard/"
        if clean_ver == "slowroll":
            return "https://download.opensuse.org/repositories/Kernel:/HEAD/standard/"
        if clean_ver == "16.1":
            return "https://download.opensuse.org/repositories/Kernel:/SL-16.1/standard/"
        if clean_ver == "16.0":
            return "https://download.opensuse.org/repositories/Kernel:/SL-16.0/standard/"
        if clean_ver == "15.6":
            return "https://download.opensuse.org/repositories/Kernel:/SLE15-SP6/standard/"
        if clean_ver == "15.5":
            return "https://download.opensuse.org/repositories/Kernel:/SLE15-SP5/standard/"
        if clean_ver.startswith("16."):
            return f"https://download.opensuse.org/repositories/Kernel:/SL-{clean_ver}/standard/"
        if clean_ver.startswith("15."):
            sp = clean_ver.split(".")[1]
            return f"https://download.opensuse.org/repositories/Kernel:/SLE15-SP{sp}/standard/"
    return ""


def build_repositories(
    distro_family: str,
    version: str,
    arch: str,
    kernel_flavor: str = "stock",
    custom_kernel_url: str = "",
) -> List[Dict[str, Any]]:
    """Construct repository dictionary configurations including base and update repositories."""
    repos: List[Dict[str, Any]] = []
    clean_ver = version.strip()

    if distro_family == "opensuse":
        clean_lower = clean_ver.lower()
        if clean_lower == "tumbleweed":
            repos.append({
                "alias": "Tumbleweed",
                "imageinclude": "true",
                "priority": "99",
                "source": "http://download.opensuse.org/tumbleweed/repo/oss/",
            })
            if kernel_flavor == "kotd":
                repos.append({
                    "alias": "kernel",
                    "imageinclude": "true",
                    "priority": "1",
                    "source": get_default_kernel_repo("opensuse", "tumbleweed"),
                })
        elif clean_lower == "slowroll":
            repos.append({
                "alias": "Slowroll_Base",
                "imageinclude": "true",
                "priority": "99",
                "source": "http://download.opensuse.org/slowroll/repo/oss/",
            })
            repos.append({
                "alias": "Slowroll_Update",
                "imageinclude": "true",
                "priority": "99",
                "source": "http://download.opensuse.org/update/slowroll/repo/oss/",
            })
            if kernel_flavor == "kotd":
                repos.append({
                    "alias": "kernel",
                    "imageinclude": "true",
                    "priority": "1",
                    "source": get_default_kernel_repo("opensuse", "slowroll"),
                })
        else:
            # Leap release
            ver_alias = clean_ver.replace(".", "_")
            repos.append({
                "alias": f"Leap_{ver_alias}",
                "imageinclude": "true",
                "priority": "99",
                "source": f"http://download.opensuse.org/distribution/leap/{clean_ver}/repo/oss/",
            })

            # Update repositories
            if clean_ver.startswith("15."):
                repos.append({
                    "alias": f"Leap_{ver_alias}_Update_OSS",
                    "imageinclude": "true",
                    "priority": "99",
                    "source": f"http://download.opensuse.org/update/leap/{clean_ver}/oss/",
                })
                repos.append({
                    "alias": "repo-sle-update",
                    "imageinclude": "true",
                    "priority": "99",
                    "source": f"http://download.opensuse.org/update/leap/{clean_ver}/sle/",
                })
                repos.append({
                    "alias": "repo-backports-update",
                    "imageinclude": "true",
                    "priority": "99",
                    "source": f"http://download.opensuse.org/update/leap/{clean_ver}/backports/",
                })
            else:
                # Leap 16.x update channel (only include if live on download.opensuse.org)
                update_url = f"http://download.opensuse.org/update/leap/{clean_ver}/oss/"
                if check_url_exists(update_url):
                    repos.append({
                        "alias": f"Leap_{ver_alias}_Update",
                        "imageinclude": "true",
                        "priority": "99",
                        "source": update_url,
                    })

            # Kernel flavor
            if kernel_flavor == "kotd":
                k_url = get_default_kernel_repo("opensuse", clean_ver)
                if k_url:
                    repos.append({
                        "alias": "kernel",
                        "imageinclude": "true",
                        "priority": "1",
                        "source": k_url,
                    })
            elif kernel_flavor == "drm-backport":
                repos.append({
                    "alias": "kernel-drm-backport",
                    "imageinclude": "true",
                    "priority": "1",
                    "source": f"https://download.opensuse.org/repositories/home:/patrikjakobsson:/kernel-sl-{clean_ver}-drm-backport/standard/",
                })

    elif distro_family == "fedora":
        f_ver = clean_ver.lower().replace("fedora-", "").replace("f", "")
        repos.append({
            "type": "rpm-md",
            "alias": f"Fedora{f_ver}",
            "sourcetype": "metalink",
            "imageinclude": "true",
            "source": f"https://mirrors.fedoraproject.org/metalink?repo=fedora-{f_ver}&arch={arch}",
        })
        repos.append({
            "type": "rpm-md",
            "alias": f"Fedora{f_ver}-updates",
            "sourcetype": "metalink",
            "imageinclude": "true",
            "source": f"https://mirrors.fedoraproject.org/metalink?repo=updates-released-f{f_ver}&arch={arch}",
        })

    if custom_kernel_url:
        repos.append({
            "alias": "kernel-custom",
            "imageinclude": "true",
            "priority": "1",
            "source": custom_kernel_url,
        })

    return repos


def get_packages(
    distro_family: str,
    arch: str,
    extra_profiles: Optional[List[str]] = None,
    version: str = "",
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """Return image and bootstrap package lists for the specified distro and architecture."""
    if extra_profiles is None:
        extra_profiles = []

    image_pkgs: List[Dict[str, str]] = []
    boot_pkgs: List[Dict[str, str]] = []

    if distro_family == "opensuse":
        # Base image packages
        base_names = [
            "checkmedia",
            "cryptsetup",
            "plymouth-theme-bgrt",
            "plymouth-dracut",
            "grub2-branding-openSUSE",
            "iputils",
            "vim",
            "grep",
            "grub2",
            "lvm2",
            "plymouth",
            "fontconfig",
            "fonts-config",
            "tar",
            "parted",
            "openssh",
            "iproute2",
            "less",
            "bash-completion",
            "dhcp-client",
            "which",
            "shim",
            "kernel-default",
            "timezone",
            "NetworkManager",
            "curl",
            "nfs-client",
            "chrony",
        ]
        for name in base_names:
            image_pkgs.append({"name": name})

        # Architecture specific
        if arch == "x86_64":
            image_pkgs.append({"name": "grub2-x86_64-efi", "arch": "x86_64"})
            image_pkgs.append({"name": "grub2-i386-pc"})
            image_pkgs.append({"name": "syslinux"})
            image_pkgs.append({"name": "kernel-firmware-amdgpu"})
            image_pkgs.append({"name": "kernel-firmware-i915"})
        elif arch == "aarch64":
            image_pkgs.append({"name": "grub2-arm64-efi", "arch": "aarch64"})
            image_pkgs.append({"name": "xz"})
            image_pkgs.append({"name": "gvfs"})

        # Extra profiles
        if "igt" in extra_profiles:
            igt_pkgs = [
                "meson", "cmake", "gcc", "git", "libdrm-devel", "libpciaccess-devel",
                "libkmod-devel", "procps-devel", "libunwind-devel", "libdw-devel",
                "libpixman-1-0-devel", "cairo-devel", "libudev-devel", "flex", "bison",
                "libjson-c-devel", "pciutils", "php", "libxmlrpc3", "xmlrpc-c-devel",
                "gsl-devel", "libcurl-devel", "liboping-devel", "alsa-devel", "screen",
                "psmisc", "pciutils-devel", "libjansson-devel",
            ]
            for p in igt_pkgs:
                image_pkgs.append({"name": p})

        if "rocm" in extra_profiles:
            rocm_pkgs = [
                "rocm-runtime", "hipcc", "rocm-hip-devel", "rocm-runtime-devel",
                "libdrm_amdgpu1", "rocminfo", "rocm-smi", "blender", "gcc-c++",
                "pciutils", "gawk", "perl-File-Which", "perl-URI", "python313",
            ]
            for p in rocm_pkgs:
                image_pkgs.append({"name": p})

        # Bootstrap packages
        boot_names = [
            "udev",
            "filesystem",
            "glibc-locale",
            "cracklib-dict-full",
            "ca-certificates",
            "ca-certificates-mozilla",
        ]
        if version.lower() == "tumbleweed":
            boot_names.extend(["grep", "diffutils", "gawk", "gzip", "openSUSE-release", "xz", "shadow"])
        elif version.startswith("15."):
            boot_names.append("openSUSE-release")

        for name in boot_names:
            boot_pkgs.append({"name": name})

    elif distro_family == "fedora":
        base_names = [
            "grub2",
            "grubby",
            "kernel",
            "plymouth-theme-charge",
            "grub2-breeze-theme",
            "selinux-policy-targeted",
            "dhclient",
            "glibc-all-langpacks",
            "vim",
            "tzdata",
            "NetworkManager",
            "chrony",
            "initial-setup",
            "dracut",
            "dracut-network",
            "@core",
            "@standard",
            "@hardware-support",
        ]
        for name in base_names:
            image_pkgs.append({"name": name})

        if arch == "aarch64":
            image_pkgs.append({"name": "@arm-tools", "arch": "aarch64"})

        boot_names = [
            "filesystem",
            "basesystem",
            "grub2-efi-x64",
            "grub2-efi-x64-modules",
            "shim-x64",
        ]
        for name in boot_names:
            boot_pkgs.append({"name": name})

    return image_pkgs, boot_pkgs


def generate_appliance_kiwi(
    name: str,
    distro_family: str,
    version: str,
    arch: str,
    kernel_flavor: str = "stock",
    custom_kernel_url: str = "",
    extra_profiles: Optional[List[str]] = None,
    password_hash: str = DEFAULT_ROOT_PASSWORD_HASH,
) -> str:
    """Generate the XML text for appliance.kiwi."""
    schema_ver = "7.6" if distro_family == "fedora" else "7.5"
    packagemanager = "dnf5" if distro_family == "fedora" else "zypper"

    root = ET.Element("image", schemaversion=schema_ver, name=f"{name}_appliance")

    # Description
    desc = ET.SubElement(root, "description", type="system")
    ET.SubElement(desc, "author").text = "MAMA"
    ET.SubElement(desc, "contact").text = "mama@local"
    ET.SubElement(desc, "specification").text = f"{distro_family.title()} {version} Appliance ({arch})"

    # Preferences
    prefs = ET.SubElement(root, "preferences")
    ET.SubElement(prefs, "type", image="kis", primary="true")
    ET.SubElement(prefs, "version").text = "1.0.0"
    ET.SubElement(prefs, "packagemanager").text = packagemanager
    ET.SubElement(prefs, "locale").text = "en_US"
    ET.SubElement(prefs, "keytable").text = "us"
    ET.SubElement(prefs, "timezone").text = "Europe/Berlin" if distro_family == "opensuse" else "UTC"
    ET.SubElement(prefs, "rpm-check-signatures").text = "false"
    if distro_family == "opensuse":
        ET.SubElement(prefs, "rpm-excludedocs").text = "true"
        ET.SubElement(prefs, "bootsplash-theme").text = "bgrt"
        ET.SubElement(prefs, "bootloader-theme").text = "openSUSE"
    else:
        ET.SubElement(prefs, "bootsplash-theme").text = "charge"
        ET.SubElement(prefs, "bootloader-theme").text = "breeze"

    # Users
    users = ET.SubElement(root, "users")
    ET.SubElement(users, "user", password=password_hash, home="/root", name="root", groups="root")

    # Repositories
    repos = build_repositories(distro_family, version, arch, kernel_flavor, custom_kernel_url)
    for r in repos:
        repo_attrs: Dict[str, str] = {}
        if "type" in r:
            repo_attrs["type"] = r["type"]
        if "alias" in r:
            repo_attrs["alias"] = r["alias"]
        if "sourcetype" in r:
            repo_attrs["sourcetype"] = r["sourcetype"]
        if "imageinclude" in r:
            repo_attrs["imageinclude"] = r["imageinclude"]
        if "priority" in r:
            repo_attrs["priority"] = r["priority"]

        repo_el = ET.SubElement(root, "repository", **repo_attrs)
        ET.SubElement(repo_el, "source", path=r["source"])

    # Packages
    img_pkgs, boot_pkgs = get_packages(distro_family, arch, extra_profiles, version=version)

    pkgs_img_el = ET.SubElement(root, "packages", type="image")
    for p in img_pkgs:
        attrs = {"name": p["name"]}
        if "arch" in p:
            attrs["arch"] = p["arch"]
        ET.SubElement(pkgs_img_el, "package", **attrs)

    pkgs_boot_el = ET.SubElement(root, "packages", type="bootstrap")
    for p in boot_pkgs:
        attrs = {"name": p["name"]}
        if "arch" in p:
            attrs["arch"] = p["arch"]
        ET.SubElement(pkgs_boot_el, "package", **attrs)

    # Pretty format XML
    raw_xml = ET.tostring(root, encoding="utf-8")
    parsed = xml.dom.minidom.parseString(raw_xml)
    return parsed.toprettyxml(indent="    ", encoding="utf-8").decode("utf-8")


def generate_config_sh(distro_family: str) -> str:
    """Generate shell script content for config.sh."""
    if distro_family == "opensuse":
        return """#!/bin/bash
# Functions...
test -f /.kconfig && . /.kconfig
test -f /.profile && . /.profile

echo "Configure image: [$kiwi_iname]..."

# Setup baseproduct link
suseSetupProduct

# Activate core services
suseInsertService sshd
suseInsertService chronyd
suseInsertService NetworkManager

# Setup default target, multi-user
baseSetRunlevel 3
"""
    else:
        return """#!/bin/bash
# Functions...
test -f /.kconfig && . /.kconfig
test -f /.profile && . /.profile

echo "Configure image: [$kiwi_iname]..."

# Activate core services
baseInsertService dbus-broker
baseInsertService NetworkManager

# Setup default target, multi-user
baseSetRunlevel 3
"""


def create_os_description(
    arch: str,
    name: str,
    distro_family: str,
    version: str,
    kernel_flavor: str = "stock",
    custom_kernel_url: str = "",
    extra_profiles: Optional[List[str]] = None,
    base_path: Optional[str] = None,
) -> pathlib.Path:
    """Create directory structure and generate Kiwi appliance files."""
    root_path = pathlib.Path(base_path or config.MAMA_PATH)
    desc_dir = root_path / "os-descriptions" / arch / name

    if desc_dir.exists():
        raise FileExistsError(f"OS description '{arch}/{name}' already exists at {desc_dir}")

    desc_dir.mkdir(parents=True, exist_ok=True)

    # 1. Write appliance.kiwi
    kiwi_content = generate_appliance_kiwi(
        name=name,
        distro_family=distro_family,
        version=version,
        arch=arch,
        kernel_flavor=kernel_flavor,
        custom_kernel_url=custom_kernel_url,
        extra_profiles=extra_profiles,
    )
    (desc_dir / "appliance.kiwi").write_text(kiwi_content, encoding="utf-8")

    # 2. Write config.sh
    config_sh_content = generate_config_sh(distro_family)
    config_sh_file = desc_dir / "config.sh"
    config_sh_file.write_text(config_sh_content, encoding="utf-8")
    try:
        config_sh_file.chmod(0o755)
    except OSError:
        pass

    # 3. Create root overlay directory
    root_overlay = desc_dir / "root"
    root_overlay.mkdir(exist_ok=True)

    if distro_family == "fedora":
        # Create fastboot marker file for faster VM / target boot
        (root_overlay / "fastboot").touch()

    # 4. Set directory group permissions
    try:
        desc_dir.chmod(0o2775)
        root_overlay.chmod(0o2775)
        mama_gid = grp.getgrnam("mama").gr_gid
        os.chown(str(desc_dir), -1, mama_gid)
        os.chown(str(root_overlay), -1, mama_gid)
    except Exception:
        pass

    return desc_dir
