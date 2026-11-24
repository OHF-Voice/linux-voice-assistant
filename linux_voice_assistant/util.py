"""Utility methods."""

import logging
import re
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Optional

# netifaces lib is from netifaces2
import netifaces
from getmac import get_mac_address  # type: ignore

_LOGGER = logging.getLogger(__name__)

# Cache for version to avoid repeated file reading
_version_cache: Optional[str] = None
_esphome_version_cache: Optional[str] = None


def get_version() -> str:
    """
    Read the version from version.txt file.

    This function reads the content safely without risk of code injection,
    as it only reads raw text and performs no evaluation.
    :return: The version from version.txt or 'unknown' if the file does not exist or cannot be read.
    """
    global _version_cache

    if _version_cache is not None:
        return _version_cache

    version_file = Path(__file__).parent.parent / "version.txt"

    try:
        # Sicher lesen: nur Rohtext, keine Evaluierung
        file_version = version_file.read_text(encoding="utf-8").strip()
        _version_cache = file_version if file_version else "unknown"
    except (FileNotFoundError, PermissionError, OSError):
        _version_cache = "unknown"

    return _version_cache


def get_esphome_version() -> str:
    """
    Read the version of the installed aioesphomeapi package.

    This function uses importlib.metadata to safely retrieve the version
    of an installed Python package without executing any code from the
    package itself.
    :return: The version of aioesphomeapi (e.g., '42.7.0'), or 'unknown' if the package is not installed or the version cannot be read.
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
    # print(f"Default interface: {interface_name}")
    return interface_name


def get_default_ipv4(interface: str):
    if not interface:
        return None

    addresses = netifaces.ifaddresses(interface)
    ipv4_info = addresses.get(netifaces.AF_INET)  # type: ignore

    if not ipv4_info:
        return None

    return ipv4_info[0]["addr"]


_MAC_ADDRESS = re.compile(r"[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}")


def resolve_mac_address(given: Optional[str], interface: Optional[str]) -> str:
    """
    The MAC address the device is known by: the one given (--mac-address), or the network
    interface's.
    :param given: MAC address from --mac-address (colon separated), or None to use the interface's.
    :param interface: Network interface to take the MAC address from when none is given.
    :raises ValueError: if the given one is not a MAC address, or the interface has none.
    """
    if given:
        if not _MAC_ADDRESS.fullmatch(given):
            raise ValueError(f"Invalid MAC address: {given} (expected for example 02:00:00:00:00:01)")
        return given.lower()
    if not (mac_address := get_mac_address(interface=interface)):
        raise ValueError("No Mac address was found")
    return str(mac_address)


def warn_about_removed_options(wake_word_dir: Optional[str], download_dir: Optional[str]) -> None:
    """
    Logs a warning for each removed option that is still given, so that an old configuration does not stop LVA from starting.
    :param wake_word_dir: Value of the removed --wake-word-dir option, or None if it is not given.
    :param download_dir: Value of the removed --download-dir option, or None if it is not given.
    """
    if wake_word_dir is not None:
        _LOGGER.warning(
            "--wake-word-dir (WAKE_WORD_DIR) '%s' is ignored. Use --wake-word-root-dir (WAKE_WORD_ROOT_DIR) and put own wake words into <root>/custom/microwakeword or <root>/custom/openwakeword",
            wake_word_dir,
        )

    if download_dir is not None:
        _LOGGER.warning("--download-dir (DOWNLOAD_DIR) '%s' is ignored. Downloaded wake words are stored in <wake word root>/downloaded", download_dir)
