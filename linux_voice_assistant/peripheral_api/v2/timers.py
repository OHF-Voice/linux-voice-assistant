"""Timers as version 2 of the peripheral API reports them.

LVA only hears about a timer when it starts, changes or finishes, so the time that is left is counted here from the end time.
The functions take the state and the clock as parameters, they do not read a clock or send anything.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple


@dataclass(frozen=True)
class TimerInfo:
    """A timer that counts down or rings."""

    name: str
    total_seconds: int
    ends_at: float
    ringing: bool = False


@dataclass(frozen=True)
class TimerEntry:
    """One timer as it is reported in the full data set."""

    id: str
    name: str
    status: str
    total_seconds: int
    seconds_left: int
    percentage: int


@dataclass(frozen=True)
class TimerSummary:
    """
    What version 2 reports about the timers.

    ``status``, ``remaining_time`` and ``remaining_percentage`` describe the timer that is nearest to ringing (or rings), ``timers``
    holds all of them, the nearest first.
    """

    status: str
    remaining_time: int
    remaining_percentage: int
    timers: Tuple[TimerEntry, ...] = ()


def update_timers(timers: Mapping[str, TimerInfo], event: str, data: Optional[Mapping[str, Any]], now: float) -> Dict[str, TimerInfo]:
    """
    Apply a timer event of LVA to the timers.
    :param timers: The timers before the event, by timer id.
    :param event: Name of the event (``timer_ticking``, ``timer_updated``, ``timer_ringing``). Other events change nothing.
    :param data: Data of the event (``id``, ``name``, ``total_seconds``, ``seconds_left``).
    :param now: Current time on the clock that ``ends_at`` is measured on.
    :return: The timers after the event. A timer with no seconds left (cancelled) is gone, one that rings is marked as ringing.
    """
    updated = dict(timers)
    if event not in ("timer_ticking", "timer_updated", "timer_ringing") or not data:
        return updated

    timer_id = str(data.get("id", ""))
    seconds_left = int(data.get("seconds_left", 0))
    name = str(data.get("name", ""))
    total_seconds = int(data.get("total_seconds", 0))
    if event == "timer_ringing":
        updated[timer_id] = TimerInfo(name, total_seconds, now, ringing=True)
    elif seconds_left <= 0:
        updated.pop(timer_id, None)
    else:
        updated[timer_id] = TimerInfo(name, total_seconds, now + seconds_left)
    return updated


def drop_stopped_timers(timers: Mapping[str, TimerInfo], ringing: bool) -> Dict[str, TimerInfo]:
    """
    Forget the timers that rang once the ringing has ended.
    :param timers: The timers by timer id.
    :param ringing: True while a timer rings.
    :return: The timers, without the ones marked as ringing if nothing rings any more.
    """
    if ringing:
        return dict(timers)
    return {timer_id: timer for timer_id, timer in timers.items() if not timer.ringing}


def summarize_timers(timers: Mapping[str, TimerInfo], now: float, ringing: bool) -> TimerSummary:
    """
    Summarize the timers for the peripheral.
    :param timers: The timers by timer id.
    :param now: Current time on the clock that ``ends_at`` is measured on.
    :param ringing: True while a timer rings.
    :return: ``status`` is ``ringing`` while one rings, otherwise ``active`` while one counts down, otherwise ``idle``. The time left and
    its percentage of the whole are those of the timer that is nearest to ringing: 0 while it rings, 0 when there is none.
    """
    entries = tuple(_entry(timer_id, timer, now) for timer_id, timer in sorted(timers.items(), key=lambda item: (not item[1].ringing, item[1].ends_at)))
    status = "ringing" if ringing else ("active" if entries else "idle")
    if not entries:
        return TimerSummary(status, 0, 0)
    nearest = entries[0]
    return TimerSummary(status, nearest.seconds_left, nearest.percentage, entries)


def _entry(timer_id: str, timer: TimerInfo, now: float) -> TimerEntry:
    remaining = 0 if timer.ringing else max(0, math.ceil(timer.ends_at - now))
    percentage = round(100 * remaining / timer.total_seconds) if timer.total_seconds > 0 else 0
    return TimerEntry(timer_id, timer.name, "ringing" if timer.ringing else "active", timer.total_seconds, remaining, max(0, min(100, percentage)))
