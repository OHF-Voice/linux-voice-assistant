"""Versions of the peripheral API and how a client picks one.

A client picks the version with the path of the WebSocket URL (``ws://host:6055/v1``). A URL without a path is version 1, so
peripherals written before versioning keep working unchanged. Every version is one module of this package.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Collection, Dict, Optional, Protocol, runtime_checkable
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from ..models import ServerState
    from .v1 import LVAEvent

DEFAULT_VERSION = "v1"
SUPPORTED_VERSIONS = ("v1", "v2")


class ApiVersionHandler(Protocol):
    """What the server needs from the module of one API version."""

    def set_state(self, state: "ServerState") -> None:
        """Attach the shared ``ServerState``."""

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Attach the running event loop."""

    async def stop(self) -> None:
        """Cancel pending work."""

    async def handle_client(self, websocket: Any) -> None:
        """Serve one client of this version until it disconnects."""

    async def emit_event(self, event: "LVAEvent", data: Optional[Dict[str, Any]] = None) -> None:
        """Offer an event of LVA to the clients of this version."""


@runtime_checkable
class StatusRefresher(Protocol):
    """A version that works out its messages from the state of LVA and has to be told when that state changed."""

    async def refresh_status(self) -> None:
        """Work out the status again and send what changed."""


@runtime_checkable
class HardwareMuteRequester(Protocol):
    """A version that can ask its peripherals to mute or unmute the hardware of the microphone."""

    async def request_hardware_mute(self, muted: bool) -> None:
        """Ask the peripherals to mute (True) or unmute (False) the hardware."""


def resolve_version(path: str, supported: Collection[str]) -> Optional[str]:
    """
    Find the API version that a request path asks for.
    :param path: Request path of the WebSocket URL, with or without a query string.
    :param supported: Names of the versions that are implemented, for example ``("v1", "v2")``.
    :return: The version name, ``DEFAULT_VERSION`` for an empty path, ``None`` if the path is not a supported version.
    """
    name = urlsplit(path).path.strip("/")
    if not name:
        return DEFAULT_VERSION
    return name if name in supported else None
