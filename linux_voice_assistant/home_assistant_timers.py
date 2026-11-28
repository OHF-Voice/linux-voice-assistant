"""Reading the timer list of Home Assistant.

Home Assistant sends no timer list when a device connects, and drops the timer events while it is disconnected. So LVA asks for the list
with the action ``timer_list.get_timers`` after every connect and merges it into its own (``timers.merge_timers()``). Home Assistant
answers a device with the timers of that device only. The format of the answer is not fixed in the specification yet, so it is read
tolerantly: times as ISO 8601 text or as seconds since the epoch, durations as seconds, ``HH:MM:SS`` or a dict of days, hours, minutes and
seconds. ``TimerSync`` keeps the state of the requests, it sends nothing itself.
"""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timezone
from typing import Any, Iterator, List, Mapping, Optional, Set

from aioesphomeapi.api_pb2 import HomeassistantActionRequest  # type: ignore[attr-defined]  # pylint: disable=no-name-in-module

from .timers import Timer, TimerStatus

_LOGGER = logging.getLogger(__name__)

GET_TIMERS_ACTION = "timer_list.get_timers"
GET_TIMERS_TIMEOUT_SECONDS = 10.0

_DURATION_TEXT = re.compile(r"^(?:(\d+)\s+days?,\s*)?(\d+):(\d{1,2}):(\d{1,2}(?:\.\d+)?)$")


def get_timers_request(call_id: int) -> HomeassistantActionRequest:
    """
    Build the request for the timer list of this device.
    :param call_id: Id that the answer of Home Assistant carries.
    """
    return HomeassistantActionRequest(service=GET_TIMERS_ACTION, wants_response=True, call_id=call_id)


def parse_timer_list(response_data: bytes, now: float) -> Optional[List[Timer]]:
    """
    Read the timers from the answer of Home Assistant.
    :param response_data: ``response_data`` of ``HomeassistantActionResponse``, JSON.
    :param now: Current time on the clock of the timer list, stored as the time of the last change.
    :return: The timers, or None if the answer is not JSON. Items that cannot be read are skipped with a warning.
    """
    try:
        data = json.loads(response_data.decode("utf-8")) if response_data else {}
    except (UnicodeDecodeError, ValueError) as error:
        _LOGGER.warning("Ignoring the timer list of Home Assistant, it is not JSON (%s): %.200r", error, response_data)
        return None

    timers = []
    for item in _timer_items(data):
        timer = timer_from_item(item, now)
        if timer is None:
            _LOGGER.warning("Ignoring a timer of Home Assistant that cannot be read: %s", item)
        else:
            timers.append(timer)
    return timers


def timer_from_item(item: Mapping[str, Any], now: float) -> Optional[Timer]:
    """
    Read one item of the timer list of Home Assistant.
    :param item: The item, with at least ``timer_id`` and ``status``.
    :param now: Current time on the clock of the timer list, stored as the time of the last change.
    :return: The timer, or None if the item lacks its id, has an unknown status or lacks the time its status needs.
    """
    try:
        status = TimerStatus(str(item.get("status", "")))
    except ValueError:
        return None
    timer_id = item.get("timer_id")
    if not timer_id:
        return None

    duration = parse_duration(item.get("total_duration"))
    if duration is None:
        duration = parse_duration(item.get("created_duration"))
    finishes_at = parse_time(item.get("finishes_at"))
    paused_remaining = parse_duration(item.get("paused_remaining"))
    if paused_remaining is None and status == TimerStatus.PAUSED:
        paused_remaining = parse_duration(item.get("remaining"))
    ended_at = parse_time(item.get("ended_at"))

    if status == TimerStatus.ACTIVE and finishes_at is None:
        return None
    if status == TimerStatus.PAUSED and paused_remaining is None:
        return None
    return Timer(
        str(timer_id),
        str(item.get("name") or ""),
        math.ceil(duration or 0.0),
        status,
        now,
        finishes_at=finishes_at if status == TimerStatus.ACTIVE else None,
        paused_remaining=paused_remaining if status == TimerStatus.PAUSED else None,
        ended_at=ended_at if status in (TimerStatus.FINISHED, TimerStatus.CANCELLED) else None,
    )


def parse_time(value: Any) -> Optional[float]:
    """
    Read a point in time.
    :param value: Seconds since the epoch, or ISO 8601 text (UTC if it has no offset).
    :return: Seconds since the epoch, or None if the value is missing or cannot be read.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.timestamp()
    return None


def parse_duration(value: Any) -> Optional[float]:
    """
    Read a duration.
    :param value: Seconds, ``[D day[s], ]HH:MM:SS[.f]`` text, or a dict of ``days``, ``hours``, ``minutes`` and ``seconds``.
    :return: Seconds, or None if the value is missing or cannot be read.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = _DURATION_TEXT.match(value.strip())
        if match is not None:
            days, hours, minutes, seconds = match.groups()
            return int(days or 0) * 86400 + int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        try:
            return float(value)
        except ValueError:
            return None
    if isinstance(value, Mapping):
        try:
            return sum(float(value.get(unit, 0)) * factor for unit, factor in (("days", 86400), ("hours", 3600), ("minutes", 60), ("seconds", 1)))
        except (TypeError, ValueError):
            return None
    return None


class TimerSync:
    """
    Keeps track of the requests for the timer list of Home Assistant. Safe to use from the event loop only.

    The caller sends the request that ``start_request()`` returns the id for and passes the answer to ``handle_response()``. If no answer
    arrives in time, it calls ``give_up()``.
    """

    def __init__(self) -> None:
        self._last_call_id = 0
        self._pending: Optional[int] = None
        self._hint_logged = False
        self.known_timer_ids: Optional[Set[str]] = None

    def start_request(self) -> int:
        """Return the id for a new request. An answer to an earlier request is ignored from now on."""
        self._last_call_id += 1
        self._pending = self._last_call_id
        _LOGGER.debug("Asking Home Assistant for its timer list (call %d)", self._pending)
        return self._pending

    def handle_response(self, call_id: int, success: bool, error_message: str, response_data: bytes, now: float) -> Optional[List[Timer]]:
        """
        Take the answer of Home Assistant to a request.
        :param call_id: Id of the request the answer belongs to.
        :param success: Whether Home Assistant could perform the action.
        :param error_message: Why it could not.
        :param response_data: The answer, JSON.
        :param now: Current time on the clock of the timer list.
        :return: The timers of Home Assistant to merge, or None if the answer belongs to no open request or carries no list.
        """
        if call_id != self._pending:
            _LOGGER.debug("Ignoring the answer of Home Assistant to call %d, the open call is %s", call_id, self._pending)
            return None
        self._pending = None
        if not success:
            self._log_hint(f"Home Assistant could not perform {GET_TIMERS_ACTION}: {error_message or 'no reason given'}")
            return None
        timers = parse_timer_list(response_data, now)
        if timers is None:
            return None
        self.known_timer_ids = {timer.timer_id for timer in timers}
        _LOGGER.debug("Home Assistant reports %d timer(s): %s", len(timers), sorted(self.known_timer_ids))
        return timers

    def give_up(self, call_id: int) -> None:
        """
        End a request that got no answer in time.
        :param call_id: Id of the request.
        """
        if call_id != self._pending:
            return
        self._pending = None
        self._log_hint(f"Home Assistant did not answer {GET_TIMERS_ACTION} within {GET_TIMERS_TIMEOUT_SECONDS:.0f} s")

    def _log_hint(self, reason: str) -> None:
        if self._hint_logged:
            _LOGGER.debug("%s. LVA keeps its own timer list", reason)
            return
        self._hint_logged = True
        _LOGGER.info(
            "%s. LVA keeps its own timer list and follows the timer events of Home Assistant. This is expected if Home Assistant has no "
            "timer list for voice timers yet, or if the ESPHome option 'Allow the device to perform Home Assistant actions' is off",
            reason,
        )


def _timer_items(data: Any) -> Iterator[Mapping[str, Any]]:
    """Find the timer items in the answer, wherever they are nested."""
    if isinstance(data, Mapping):
        if "timer_id" in data:
            yield data
            return
        for value in data.values():
            yield from _timer_items(value)
    elif isinstance(data, list):
        for value in data:
            yield from _timer_items(value)
