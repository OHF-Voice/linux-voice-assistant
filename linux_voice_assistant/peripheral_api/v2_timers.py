"""Timers as version 2 of the peripheral API reports them.

LVA only hears about a timer when it starts, changes or finishes, so the time that is left is counted here from the end time.
The functions take the state and the clock as parameters, they do not read a clock or send anything.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional


@dataclass(frozen=True)
class TimerInfo:
    """A counting-down timer."""

    total_seconds: int
    ends_at: float


@dataclass(frozen=True)
class TimerSummary:
    """What version 2 reports about the timers."""

    status: str
    remaining_time: int
    remaining_percentage: int


def update_timers(timers: Mapping[str, TimerInfo], event: str, data: Optional[Mapping[str, Any]], now: float) -> Dict[str, TimerInfo]:
    """
    Apply a timer event of LVA to the counting-down timers.
    :param timers: The timers before the event, by timer id.
    :param event: Name of the event (``timer_ticking``, ``timer_updated``, ``timer_ringing``). Other events change nothing.
    :param data: Data of the event (``id``, ``total_seconds``, ``seconds_left``).
    :param now: Current time on the clock that ``ends_at`` is measured on.
    :return: The timers after the event. A timer with no seconds left (cancelled) or one that rings is no longer counting down.
    """
    updated = dict(timers)
    if event not in ("timer_ticking", "timer_updated", "timer_ringing") or not data:
        return updated

    timer_id = str(data.get("id", ""))
    seconds_left = int(data.get("seconds_left", 0))
    if event == "timer_ringing" or seconds_left <= 0:
        updated.pop(timer_id, None)
    else:
        updated[timer_id] = TimerInfo(total_seconds=int(data.get("total_seconds", 0)), ends_at=now + seconds_left)
    return updated


def summarize_timers(timers: Mapping[str, TimerInfo], now: float, ringing: bool) -> TimerSummary:
    """
    Summarize the timers for the peripheral.
    :param timers: The counting-down timers by timer id.
    :param now: Current time on the clock that ``ends_at`` is measured on.
    :param ringing: True while a timer rings.
    :return: ``ringing`` while one rings, otherwise ``active`` while one counts down, otherwise ``idle``. The time left and its
    percentage of the whole are those of the timer that ends first, 0 when none counts down.
    """
    status = "ringing" if ringing else ("active" if timers else "idle")
    if not timers:
        return TimerSummary(status, 0, 0)

    first = min(timers.values(), key=lambda timer: timer.ends_at)
    remaining = max(0, math.ceil(first.ends_at - now))
    percentage = round(100 * remaining / first.total_seconds) if first.total_seconds > 0 else 0
    return TimerSummary(status, remaining, max(0, min(100, percentage)))
