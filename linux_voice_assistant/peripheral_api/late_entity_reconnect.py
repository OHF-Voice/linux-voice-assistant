"""Reconnect Home Assistant once after a peripheral added an entity.

Home Assistant only lists the entities of a device when it connects. An entity that appears later is only seen after a new
connection, so LVA closes the connection once and Home Assistant connects again. The reconnect waits a moment, so several entities
that arrive together cause one reconnect, and is not repeated within a cooldown.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..models import ServerState

_LOGGER = logging.getLogger(__name__)


class LateEntityReconnect:
    """Closes the connection to Home Assistant once for entities that were added after the handshake."""

    DEBOUNCE_S: float = 1.5
    COOLDOWN_S: float = 60.0

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._last_reconnect_at: float = 0.0

    def schedule(self, state: "ServerState", loop: Optional[asyncio.AbstractEventLoop], entity_kind: str, entity_id: str) -> None:
        """
        Plan the reconnect, unless Home Assistant is not connected or one is already planned.
        :param state: The server state, with the connection to Home Assistant.
        :param loop: The running event loop, None if the server has not started.
        :param entity_kind: What was added, for the log.
        :param entity_id: The object id of the entity, for the log.
        """
        if not state.connected or state.satellite is None or loop is None:
            return

        if self._task is not None and not self._task.done():
            _LOGGER.debug("Coalescing late %s registration for '%s' into pending HA reconnect", entity_kind, entity_id)
            return

        self._task = loop.create_task(self._reconnect(state, entity_kind, entity_id))

    def cancel(self) -> None:
        """Drop a planned reconnect."""
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _reconnect(self, state: "ServerState", entity_kind: str, entity_id: str) -> None:
        try:
            await asyncio.sleep(self.DEBOUNCE_S)
            if not state.connected or state.satellite is None:
                return

            now = time.monotonic()
            if now - self._last_reconnect_at < self.COOLDOWN_S:
                _LOGGER.info("Skipping HA reconnect after late %s '%s': cooldown active", entity_kind, entity_id)
                return

            transport = getattr(state.satellite, "_transport", None)
            if transport is None:
                return

            self._last_reconnect_at = now
            _LOGGER.info("Late %s '%s' registered after HA handshake; forcing one HA reconnect", entity_kind, entity_id)
            transport.close()
        except asyncio.CancelledError:
            return
