"""Utility methods."""

import uuid
import netifaces2
from collections.abc import Callable
from typing import Optional

def call_all(*callables: Optional[Callable[[], None]]) -> None:
    for item in filter(None, callables):
        item()

def get_default_interface():
    gateways = netifaces2.gateways()

    # Get default IPv4 gateway
    default_gateway = gateways.get("default", {}).get(netifaces2.AF_INET)

    if default_gateway:
        gateway_ip, interface = default_gateway
        return interface

    return None

def get_default_ipv4():
    default = get_default_interface()

    if not default:
        return None

    gateway_ip, interface = default

    addresses = netifaces2.ifaddresses(interface)
    ipv4_info = addresses.get(netifaces2.AF_INET)

    if not ipv4_info:
        return None

    return ipv4_info[0]["addr"]
