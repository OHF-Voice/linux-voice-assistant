"""Timers as version 2 of the peripheral API reports them.

Version 2 keeps no timers of its own: it reports the timer list of LVA (``timers.py``). The functions take the timers and the clock as
parameters, they do not read a clock or send anything.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

from ...timers import Timer, TimerStatus, display_timer


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

    ``status``, ``remaining_time`` and ``remaining_percentage`` describe the timer that is shown, ``timers`` holds all timers that ring,
    count down or are paused, the shown one first.
    """

    status: str
    remaining_time: int
    remaining_percentage: int
    timers: Tuple[TimerEntry, ...] = ()


def summarize_timers(timers: Mapping[str, Timer], now: float) -> TimerSummary:
    """
    Summarize the timer list for the peripheral.
    :param timers: The timer list by timer id.
    :param now: Current time on the clock of the timer list.
    :return: ``status`` is ``ringing`` while a timer rings, otherwise ``active`` while one counts down, otherwise ``paused`` while one is
        paused, otherwise ``idle``. The time left and its percentage of the whole are those of the shown timer (see
        ``timers.display_timer()``): 0 while it rings, 0 when there is none. Ended timers that no longer ring are not listed.
    """
    entries = tuple(_entry(timer, now) for timer in sorted((timer for timer in timers.values() if _listed(timer)), key=lambda timer: _order(timer, now)))
    shown: Optional[Timer] = display_timer(timers)
    if shown is None:
        return TimerSummary("idle", 0, 0, entries)
    entry = _entry(shown, now)
    return TimerSummary(entry.status, entry.seconds_left, entry.percentage, entries)


def has_running_timer(timers: Mapping[str, Timer]) -> bool:
    """
    Tell whether the time left of a timer changes on its own.
    :param timers: The timer list by timer id.
    :return: True while a timer counts down.
    """
    return any(timer.status == TimerStatus.ACTIVE for timer in timers.values())


def _listed(timer: Timer) -> bool:
    return timer.ringing or timer.status in (TimerStatus.ACTIVE, TimerStatus.PAUSED)


def _order(timer: Timer, now: float) -> Tuple[int, float]:
    if timer.ringing:
        return 0, timer.ringing_since or 0.0
    if timer.status == TimerStatus.ACTIVE:
        return 1, (timer.finishes_at or now) - now
    return 2, timer.paused_remaining or 0.0


def _entry(timer: Timer, now: float) -> TimerEntry:
    if timer.ringing:
        status, remaining = "ringing", 0
    elif timer.status == TimerStatus.ACTIVE:
        status, remaining = "active", max(0, math.ceil((timer.finishes_at or now) - now))
    else:
        status, remaining = "paused", max(0, math.ceil(timer.paused_remaining or 0.0))
    percentage = round(100 * remaining / timer.total_seconds) if timer.total_seconds > 0 else 0
    return TimerEntry(timer.timer_id, timer.name, status, timer.total_seconds, remaining, max(0, min(100, percentage)))
