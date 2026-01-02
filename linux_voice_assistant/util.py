"""Utility methods."""

import os
import uuid
from collections.abc import Callable
from typing import Optional


def get_mac() -> str:
    """Get MAC address. Uses LVA_MAC_ADDRESS env var if set (for Docker), otherwise system MAC."""
    env_mac = os.environ.get("LVA_MAC_ADDRESS")
    if env_mac:
        return env_mac
    
    mac = uuid.getnode()
    mac_str = ":".join(f"{(mac >> i) & 0xff:02x}" for i in range(40, -1, -8))
    return mac_str


def call_all(*callables: Optional[Callable[[], None]]) -> None:
    for item in filter(None, callables):
        item()
