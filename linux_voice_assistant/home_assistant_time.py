"""The time of Home Assistant, which LVA compares its timers on.

The end time of a timer comes from Home Assistant, so LVA compares it on the clock of Home Assistant and not on its own, which can be off
(for example before it is set over the network). LVA asks Home Assistant for its time with the ESPHome ``GetTimeRequest`` after connecting
and then every 30 s, and keeps the difference to its own clock. Until the first answer, the difference is 0.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Iterable

from aioesphomeapi.api_pb2 import GetTimeRequest  # type: ignore[attr-defined]  # pylint: disable=no-name-in-module
from google.protobuf import message

_LOGGER = logging.getLogger(__name__)

TIME_REQUEST_INTERVAL_SECONDS = 30.0
MAX_CLOCK_OFFSET_SECONDS = 10.0


def clock_offset(epoch_seconds: int, local_now: float) -> float:
    """
    Work out how far the clock of Home Assistant is ahead of the own clock.
    :param epoch_seconds: Time of Home Assistant from ``GetTimeResponse``, in whole seconds (cut off, not rounded).
    :param local_now: Own wall clock when the answer arrived, in seconds.
    :return: Seconds to add to the own clock to get the time of Home Assistant. Accurate to about half a second.
    """
    return epoch_seconds + 0.5 - local_now


def check_clock_offset(offset: float, max_offset: float = MAX_CLOCK_OFFSET_SECONDS) -> bool:
    """
    Log the difference between the clocks, as an error if it is too large.
    :param offset: Seconds the clock of Home Assistant is ahead of the own clock.
    :param max_offset: Largest difference that is not reported as an error.
    :return: True if the difference is at most ``max_offset``.
    """
    if abs(offset) > max_offset:
        _LOGGER.error(
            "The clock of this device differs from the clock of Home Assistant by %.1f s (more than %.1f s). Timers use the time of Home Assistant, check the time setup of this device",
            offset,
            max_offset,
        )
        return False
    _LOGGER.debug("Clock of Home Assistant is %.1f s ahead of the own clock (limit %.1f s)", offset, max_offset)
    return True


async def request_time_periodically(send: Callable[[Iterable[message.Message]], None], interval: float = TIME_REQUEST_INTERVAL_SECONDS) -> None:
    """
    Ask Home Assistant for its time now and then every ``interval`` seconds, until cancelled.
    :param send: Sends messages to Home Assistant.
    :param interval: Seconds between two requests.
    """
    while True:
        _LOGGER.debug("Asking Home Assistant for its time")
        send([GetTimeRequest()])
        await asyncio.sleep(interval)
