"""Utility methods."""

import uuid
# netifaces lib is from netifaces2
import netifaces
from collections.abc import Callable
from typing import Optional

def call_all(*callables: Optional[Callable[[], None]]) -> None:
    for item in filter(None, callables):
        item()


def get_default_interface():
    gateways = netifaces.gateways()

    # Get default IPv4 gateway
    default_gateway = gateways['default'][netifaces.AF_INET]

    if not default_gateway:
        return None

    return default_gateway


def get_default_ipv4(interface: str):
    if not interface:
        return None

    addresses = netifaces.ifaddresses(interface)
    ipv4_info = addresses.get(netifaces.AF_INET)

    if not ipv4_info:
        return None

    return ipv4_info[0]["addr"]
