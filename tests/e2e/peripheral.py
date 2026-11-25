"""A peripheral client for the end-to-end tests.

LVA reports its state (wake word detected, listening, speaking, idle) to peripherals over a WebSocket.
The tests use that stream to follow what LVA is doing, the same way an LED ring would.
"""

import asyncio
import json
from typing import Any, Dict, List, Optional

import websockets


class PeripheralClient:
    """Collects the events of the peripheral API and lets tests wait for them."""

    #: Time to let the replay of the current state event after the snapshot arrive, so it is not taken for a new event
    REPLAY_SECONDS = 0.3

    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []
        self.snapshot: Dict[str, Any] = {}
        self._connection: Any = None
        self._task: Optional[asyncio.Task] = None

    async def connect(self, port: int) -> None:
        self._connection = await websockets.connect(f"ws://127.0.0.1:{port}")
        self._task = asyncio.create_task(self._collect())
        snapshot = await self.wait_for("snapshot", 0, timeout=5)
        self.snapshot = snapshot["data"]
        await asyncio.sleep(self.REPLAY_SECONDS)

    async def disconnect(self) -> None:
        if self._task is not None:
            self._task.cancel()
        if self._connection is not None:
            await self._connection.close()

    def mark(self) -> int:
        """Return a position that later waits use as their start, so earlier events are ignored."""
        return len(self.events)

    async def command(self, name: str, data: Optional[Dict[str, Any]] = None) -> None:
        await self._connection.send(json.dumps({"command": name, "data": data or {}}))

    async def wait_for(self, event: str, since: int, timeout: float = 15.0) -> Dict[str, Any]:
        """
        Wait for an event after the mark.
        :param event: Event name, for example ``wake_word_detected``.
        :param since: Position from :meth:`mark`.
        :param timeout: Seconds to wait.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            for entry in self.events[since:]:
                if entry.get("event") == event:
                    return entry
            if loop.time() >= deadline:
                raise TimeoutError(f"timed out after {timeout} s waiting for event {event!r}, got {self.names_since(since)}")
            await asyncio.sleep(0.05)

    def names_since(self, since: int) -> List[str]:
        return [entry.get("event", "") for entry in self.events[since:]]

    async def _collect(self) -> None:
        async for message in self._connection:
            self.events.append(json.loads(message))
