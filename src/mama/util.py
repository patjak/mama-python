from __future__ import annotations

import getpass
import ipaddress
import os
import pathlib
import re
import sys
from typing import Any, List, Optional, Union


class Util:
    ANSI_COLORS: List[int] = [31, 32, 33, 34, 35, 36, 91, 92, 93, 94, 95, 96]

    @staticmethod
    def get_user() -> str:
        """Return the active username, prioritizing SUDO_USER when executed under sudo."""
        sudo_user = os.environ.get("SUDO_USER")
        if sudo_user:
            return sudo_user
        try:
            return getpass.getuser()
        except Exception:
            return os.environ.get("USER") or os.environ.get("LOGNAME") or "unknown"

    @staticmethod
    def is_root() -> bool:
        """Check if the current process is running with root privileges."""
        try:
            return os.geteuid() == 0
        except AttributeError:
            return False

    @staticmethod
    def is_valid_ip(ip: str) -> Union[str, bool]:
        """Validate IPv4 address. Returns the IP string if valid, otherwise False."""
        if not ip:
            return False
        try:
            ipaddress.IPv4Address(ip)
            return ip
        except ValueError:
            return False

    @staticmethod
    def is_valid_mac(mac: str) -> bool:
        """Validate MAC address format (XX:XX:XX:XX:XX:XX or XX-XX-XX-XX-XX-XX)."""
        if not mac:
            return False
        pattern = r"^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$"
        return bool(re.match(pattern, mac.strip()))

    @staticmethod
    def pad_str(val: Any, length: int) -> str:
        """Pad a string or value with trailing spaces to the specified length."""
        return str(val if val is not None else "").ljust(length)

    @staticmethod
    def parse_after(prefix: str, text: str) -> Union[str, bool]:
        """
        If text starts with prefix, returns the remainder of the string.
        Otherwise returns False.
        """
        if text.startswith(prefix):
            return text[len(prefix) :]
        return False

    @staticmethod
    def string_to_color(text: str, color_code: int = 0) -> str:
        """Wrap text in ANSI color escape codes based on a numeric color index."""
        code_map = {0: 31, 1: 32, 2: 33, 3: 34, 4: 36, 5: 35}
        ansi_num = code_map.get(color_code, 31)
        return f"\033[{ansi_num}m{text}\033[0m"

    @staticmethod
    def string_to_rand_color(val: Any) -> str:
        """Hash input value to choose a consistent pseudo-random ANSI color."""
        s_val = str(val)
        hash_val = sum(ord(c) for c in s_val)
        color = Util.ANSI_COLORS[hash_val % len(Util.ANSI_COLORS)]
        return f"\033[{color}m{s_val}\033[0m"

    @staticmethod
    def get_line(prompt: str) -> str:
        """Print prompt and read a line from standard input."""
        try:
            return input(prompt)
        except (EOFError, KeyboardInterrupt):
            return ""

    @staticmethod
    def ask_yesno(prompt: str) -> str:
        """Prompt user for a yes/no response. Returns 'y' or 'n'."""
        while True:
            resp = Util.get_line(f"{prompt} (y/n): ").strip().lower()
            if resp in ("y", "yes"):
                return "y"
            elif resp in ("n", "no"):
                return "n"

    @staticmethod
    def ask_number(max_val: int, prompt: str) -> int:
        """Prompt user to select a number between 1 and max_val."""
        while True:
            val_str = Util.get_line(prompt).strip()
            if val_str.isdigit():
                num = int(val_str)
                if 1 <= num <= max_val:
                    return num
            print(f"Please enter a number between 1 and {max_val}.")

    @staticmethod
    def ask_from_array(items: List[Any], prompt: str) -> Any:
        """Display item menu and prompt user to pick an element from a list."""
        if not items:
            return None

        while True:
            resp = Util.get_line(f"{prompt} ").strip()
            if resp.isdigit():
                idx = int(resp) - 1
                if 0 <= idx < len(items):
                    return items[idx]
            for item in items:
                item_name = getattr(item, "name", str(item))
                if resp == item_name or resp == str(item):
                    return item
            print(f"Invalid selection: '{resp}'. Please select a valid index or name.")

    @staticmethod
    def get_directory_contents(base_path: str, depth: int = 1) -> List[str]:
        """
        Recursively scan directory up to specified depth, returning relative paths.
        Ignores hidden files and directories starting with '.'
        """
        results: List[str] = []
        p_base = pathlib.Path(base_path)

        if not p_base.exists() or not p_base.is_dir():
            return results

        def _scan(current_path: pathlib.Path, current_depth: int) -> None:
            try:
                entries = sorted([e for e in current_path.iterdir() if not e.name.startswith(".")])
            except PermissionError:
                return

            for entry in entries:
                rel_path = entry.relative_to(p_base).as_posix()
                if current_depth == depth:
                    results.append(rel_path)
                elif current_depth < depth and entry.is_dir():
                    _scan(entry, current_depth + 1)

        _scan(p_base, 1)
        return sorted(results)