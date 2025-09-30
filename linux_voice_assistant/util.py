"""Utility methods."""

import platform
import uuid
from collections.abc import Callable
from typing import Optional
import os


def get_mac() -> str:
    # Check for fixed MAC in environment variable
    mac_env = os.getenv("FIXED_MAC_ADDRESS")
    if mac_env:
        return mac_env.lower()
    # Fallback to uuid.getnode()
    mac = uuid.getnode()
    mac_str = ":".join(f"{(mac >> i) & 0xff:02x}" for i in range(40, -1, -8))
    return mac_str


def call_all(*callables: Optional[Callable[[], None]]) -> None:
    for item in filter(None, callables):
        item()


def is_arm() -> bool:
    machine = platform.machine()
    return ("arm" in machine) or ("aarch" in machine)
