"""WebSocket server of the peripheral API.

Binds the port and hands every client to the module of the API version it asked for. What a version sends and accepts lives in
the module of that version (``v1.py``), so one version never changes another.
"""

from __future__ import annotations

import asyncio
import logging
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Dict, Optional

from .v1 import LVAEvent, PeripheralApiV1
from .versions import ApiVersionHandler, resolve_version

if TYPE_CHECKING:
    from ..models import ServerState

_LOGGER = logging.getLogger(__name__)


class PeripheralAPIServer:
    """
    WebSocket server that bridges LVA state to peripheral containers, one API version per connection.

    Usage
    -----
    1. Construct in ``__main__.main()``.
    2. ``peripheral_api.set_state(state)`` once ``ServerState`` exists.
    3. ``await peripheral_api.start()`` inside the running event loop.
    4. Call ``emit_event_sync()`` from any thread (mpv callbacks, audio thread).
    """

    DEFAULT_VOLUME_STEP: float = PeripheralApiV1.DEFAULT_VOLUME_STEP

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 6055,
        volume_step: float = DEFAULT_VOLUME_STEP,
    ) -> None:
        self._host = host
        self._port = port
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server: Any = None
        self._versions: Dict[str, ApiVersionHandler] = {"v1": PeripheralApiV1(volume_step=volume_step)}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def set_state(self, state: "ServerState") -> None:
        """Attach the shared ``ServerState`` so commands can read/mutate it."""
        for handler in self._versions.values():
            handler.set_state(state)

    async def start(self) -> None:
        """Start the WebSocket server inside the running event loop."""
        try:
            from websockets.server import serve  # type: ignore[import]
        except ImportError:
            _LOGGER.error("websockets package not installed – peripheral API disabled. Install with: pip install websockets")
            return

        self._loop = asyncio.get_running_loop()
        for handler in self._versions.values():
            handler.set_loop(self._loop)
        self._server = await serve(self._handle_client, self._host, self._port, process_request=self._process_request)
        _LOGGER.info("Peripheral API listening at ws://%s:%d (versions: %s)", self._host, self._port, ", ".join(self._versions))

    async def stop(self) -> None:
        """Gracefully shut down the server and all client connections."""
        for handler in self._versions.values():
            await handler.stop()

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            _LOGGER.info("Peripheral API stopped")

    # ------------------------------------------------------------------
    # Event emission
    # ------------------------------------------------------------------

    async def emit_event(
        self,
        event: LVAEvent,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Offer an event to every API version, which sends it to its own clients."""
        for handler in self._versions.values():
            await handler.emit_event(event, data)

    def emit_event_sync(
        self,
        event: LVAEvent,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Thread-safe fire-and-forget event emission.

        Safe to call from mpv callbacks, the audio processing thread, or any
        non-async context while the asyncio event loop is running.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self.emit_event(event, data), loop)

    # ------------------------------------------------------------------
    # Client handling
    # ------------------------------------------------------------------

    async def _handle_client(self, websocket: Any) -> None:
        version = resolve_version(getattr(websocket, "path", ""), self._versions)
        if version is None:
            return
        _LOGGER.debug("Peripheral client uses API version %s", version)
        await self._versions[version].handle_client(websocket)

    async def _process_request(self, path: str, _request_headers: Any) -> Optional[tuple]:
        """Refuse a path that is not a supported version before the WebSocket handshake."""
        if resolve_version(path, self._versions) is not None:
            return None
        _LOGGER.warning("Peripheral client asked for unsupported API version '%s' (supported: %s)", path, ", ".join(self._versions))
        return HTTPStatus.NOT_FOUND, [], f"Unsupported peripheral API version. Supported: {', '.join(self._versions)}\n".encode()
