"""Utility methods."""

import uuid
# netifaces lib is from netifaces2
import netifaces
from collections.abc import Callable
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path
from typing import Optional

# Cache for version to avoid repeated file reading
_version_cache: Optional[str] = None
_esphome_version_cache: Optional[str] = None


def get_version() -> str:
    """
    Read the version from version.txt file.
    
    This function reads the content safely without risk of code injection,
    as it only reads raw text and performs no evaluation.
    
    Returns:
        str: The version from version.txt or 'unknown' if the file
             does not exist or cannot be read.
    """
    global _version_cache
    
    if _version_cache is not None:
        return _version_cache
    
    version_file = Path(__file__).parent.parent / "version.txt"
    
    try:
        # Sicher lesen: nur Rohtext, keine Evaluierung
        version = version_file.read_text(encoding="utf-8").strip()
        _version_cache = version if version else "unknown"
    except (FileNotFoundError, PermissionError, OSError):
        _version_cache = "unknown"
    
    return _version_cache


def get_esphome_version() -> str:
    """
    Read the version of the installed aioesphomeapi package.
    
    This function uses importlib.metadata to safely retrieve the version
    of an installed Python package without executing any code from the
    package itself.
    
    Returns:
        str: The version of aioesphomeapi (e.g., '42.7.0'), or 'unknown'
             if the package is not installed or the version cannot be read.
    """
    global _esphome_version_cache
    
    if _esphome_version_cache is not None:
        return _esphome_version_cache
    
    try:
        _esphome_version_cache = version("aioesphomeapi")
    except PackageNotFoundError:
        _esphome_version_cache = "unknown"
    
    return _esphome_version_cache


def call_all(*callables: Optional[Callable[[], None]]) -> None:
    for item in filter(None, callables):
        item()


def get_default_interface():
    """Return the default network interface name, or None if not found."""
    default_gateway = netifaces.default_gateway()

    if not default_gateway:
        print("No default gateway found")
        return None

    # default_gateway is e.g. {InterfaceType.AF_INET: ('192.168.33.1', 'wlp0s20f3')}
    gateway_info = default_gateway.get(netifaces.AF_INET)
    if not gateway_info:
        print("No default IPv4 gateway found")
        return None

    # gateway_info is a tuple: (gateway_ip, interface_name)
    interface_name = gateway_info[1]
    #print(f"Default interface: {interface_name}")
    return interface_name


def get_default_ipv4(interface: str):
    if not interface:
        return None

    addresses = netifaces.ifaddresses(interface)
    ipv4_info = addresses.get(netifaces.AF_INET)

    if not ipv4_info:
        return None

    return ipv4_info[0]["addr"]
