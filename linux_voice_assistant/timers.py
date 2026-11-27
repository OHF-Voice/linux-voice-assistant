"""The list of voice timers that LVA keeps on its own.

Home Assistant keeps the timers of a device in a ``timer_list`` entity and tells the satellite about every change, but only while it is
connected. LVA keeps its own list with the same statuses, so a timer still rings while Home Assistant is offline and survives a restart
of LVA. The functions take the timers and the clock as parameters and return the result. They start no threads and do no I/O: ringing,
storing and sending are left to the caller. ``TimerList`` only holds the current timers for the caller.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple

from aioesphomeapi.model import VoiceAssistantTimerEventType

_LOGGER = logging.getLogger(__name__)

ARCHIVE_SECONDS = 7 * 24 * 60 * 60


class TimerStatus(str, Enum):
    """Status of a timer, with the values of the Home Assistant ``timer_list`` entity."""

    ACTIVE = "active"
    PAUSED = "paused"
    FINISHED = "finished"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Timer:
    """
    One timer of the list. All times are wall-clock seconds on the clock the caller passes as ``now``.

    ``finishes_at`` is set only while ``active``, ``paused_remaining`` only while ``paused``, ``ended_at`` once it is ``finished`` or
    ``cancelled``. ``ringing_since`` is only known locally: it is set while the timer rings.
    """

    timer_id: str
    name: str
    total_seconds: int
    status: TimerStatus
    changed_at: float
    finishes_at: Optional[float] = None
    paused_remaining: Optional[float] = None
    ended_at: Optional[float] = None
    ringing_since: Optional[float] = None

    @property
    def ringing(self) -> bool:
        """True while the timer rings."""
        return self.ringing_since is not None


@dataclass(frozen=True)
class TimerEvent:
    """A timer event of Home Assistant (``VoiceAssistantTimerEventResponse``)."""

    event_type: VoiceAssistantTimerEventType
    timer_id: str
    name: str
    total_seconds: int
    seconds_left: int
    is_active: bool


def apply_timer_event(timers: Mapping[str, Timer], event: TimerEvent, now: float) -> Dict[str, Timer]:
    """
    Apply a timer event of Home Assistant to the timers.
    :param timers: The timers before the event, by timer id.
    :param event: The event.
    :param now: Current time.
    :return: The timers after the event.

    ``started`` and ``updated`` set the end time from the seconds left, ``updated`` with ``is_active`` false pauses the timer. ``cancelled``
    ends it silently. ``finished`` makes it due now, so the countdown rings it, unless it is ``finished`` already (ringing or not, Home
    Assistant sends ``finished`` once per timer). Unknown timers are added, except by ``cancelled``.
    """
    updated = dict(timers)
    current = timers.get(event.timer_id)
    event_type = event.event_type

    if event_type == VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_CANCELLED:
        if current is None:
            _LOGGER.debug("Timer '%s' cancelled, but it is not in the list", event.timer_id)
            return updated
        updated[event.timer_id] = _cancelled(current, now)
        _LOGGER.debug("Timer '%s' (%s) cancelled", event.timer_id, event.name)
        return updated

    if event_type == VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_FINISHED:
        if current is not None and current.ringing:
            _LOGGER.debug("Timer '%s' (%s) finished, but it rings already", event.timer_id, event.name)
            return updated
        if current is not None and current.status == TimerStatus.FINISHED:
            _LOGGER.debug("Timer '%s' (%s) finished, but it finished already, it does not ring again", event.timer_id, event.name)
            return updated
        updated[event.timer_id] = _active(event, finishes_at=now, now=now)
        _LOGGER.debug("Timer '%s' (%s) finished in Home Assistant, it is due now", event.timer_id, event.name)
        return updated

    if event_type == VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_UPDATED and not event.is_active:
        updated[event.timer_id] = Timer(event.timer_id, event.name, event.total_seconds, TimerStatus.PAUSED, now, paused_remaining=float(event.seconds_left))
        _LOGGER.debug("Timer '%s' (%s) paused with %d s left", event.timer_id, event.name, event.seconds_left)
        return updated

    updated[event.timer_id] = _active(event, finishes_at=now + event.seconds_left, now=now)
    _LOGGER.debug("Timer '%s' (%s) %s, finishes in %d s", event.timer_id, event.name, "started" if current is None else "updated", event.seconds_left)
    return updated


def due_timers(timers: Mapping[str, Timer], now: float) -> Tuple[str, ...]:
    """
    Find the timers whose time is up.
    :param timers: The timers by timer id.
    :param now: Current time.
    :return: Ids of the ``active`` timers whose end time has passed, the earliest first.
    """
    due = [timer for timer in timers.values() if timer.status == TimerStatus.ACTIVE and timer.finishes_at is not None and timer.finishes_at <= now]
    return tuple(timer.timer_id for timer in sorted(due, key=lambda timer: timer.finishes_at or 0.0))


def start_ringing(timers: Mapping[str, Timer], timer_ids: Iterable[str], now: float) -> Dict[str, Timer]:
    """
    Mark timers as ringing.
    :param timers: The timers by timer id.
    :param timer_ids: The timers that start ringing. Unknown ids are skipped.
    :param now: Current time, stored as the start of the ringing and as the end of the timer.
    :return: The timers, the given ones ``finished`` and ringing.
    """
    updated = dict(timers)
    for timer_id in timer_ids:
        timer = timers.get(timer_id)
        if timer is None:
            continue
        updated[timer_id] = replace(timer, status=TimerStatus.FINISHED, finishes_at=None, paused_remaining=None, ended_at=now, ringing_since=now, changed_at=now)
        _LOGGER.debug("Timer '%s' (%s) starts ringing", timer_id, timer.name)
    return updated


def ringing_timers(timers: Mapping[str, Timer]) -> Tuple[str, ...]:
    """
    Find the timers that ring.
    :param timers: The timers by timer id.
    :return: Ids of the ringing timers, the one that rings longest first.
    """
    ringing = [timer for timer in timers.values() if timer.ringing]
    return tuple(timer.timer_id for timer in sorted(ringing, key=lambda timer: timer.ringing_since or 0.0))


def stop_ringing(timers: Mapping[str, Timer], timer_ids: Iterable[str], now: float) -> Dict[str, Timer]:
    """
    End the ringing of timers. They stay ``finished`` and never ring again.
    :param timers: The timers by timer id.
    :param timer_ids: The timers whose ringing ends. Ids that are unknown or do not ring are skipped.
    :param now: Current time.
    :return: The timers after the ringing ended.
    """
    updated = dict(timers)
    for timer_id in timer_ids:
        timer = timers.get(timer_id)
        if timer is None or not timer.ringing:
            continue
        updated[timer_id] = replace(timer, ringing_since=None, changed_at=now)
        _LOGGER.debug("Timer '%s' (%s) stops ringing after %.1f s", timer_id, timer.name, now - (timer.ringing_since or now))
    return updated


def auto_stopped_timers(timers: Mapping[str, Timer], now: float, max_ring_seconds: float) -> Tuple[str, ...]:
    """
    Find the timers that have rung long enough.
    :param timers: The timers by timer id.
    :param now: Current time.
    :param max_ring_seconds: How long a timer rings at most, counted from the start of its ringing.
    :return: Ids of the ringing timers that started ringing at least ``max_ring_seconds`` ago.
    """
    stopped = []
    for timer_id in ringing_timers(timers):
        rung = now - (timers[timer_id].ringing_since or now)
        if rung >= max_ring_seconds:
            _LOGGER.debug("Timer '%s' rang for %.1f s, stopping it on its own (limit %.1f s)", timer_id, rung, max_ring_seconds)
            stopped.append(timer_id)
    return tuple(stopped)


def restart_ringing(timers: Mapping[str, Timer], now: float) -> Dict[str, Timer]:
    """
    Let the timers that rang before a restart of LVA ring again for the full time.
    :param timers: The timers as they were stored.
    :param now: Current time, stored as the new start of the ringing.
    :return: The timers, the ringing ones with their ringing starting now.
    """
    updated = dict(timers)
    for timer_id in ringing_timers(timers):
        updated[timer_id] = replace(timers[timer_id], ringing_since=now, changed_at=now)
        _LOGGER.debug("Timer '%s' rang before the restart, it rings again", timer_id)
    return updated


def merge_timers(local: Mapping[str, Timer], remote: Iterable[Timer], now: float) -> Dict[str, Timer]:
    """
    Merge the timer list of Home Assistant into the local one.
    :param local: The local timers by timer id.
    :param remote: The timers Home Assistant reports.
    :param now: Current time.
    :return: The merged timers.

    A remote ``active`` or ``paused`` timer is taken over with its values. A remote ``finished`` timer that runs locally becomes due, so the
    countdown rings it. A remote ``cancelled`` timer is cancelled locally. Remote ``finished`` or ``cancelled`` timers that are not local are
    ignored. Local timers that Home Assistant does not report are kept, because Home Assistant loses its timers on a restart.
    """
    merged = dict(local)
    for theirs in remote:
        ours = local.get(theirs.timer_id)
        result = _merged_timer(ours, theirs, now)
        if result is None or result == ours:
            continue
        merged[theirs.timer_id] = result
        _LOGGER.debug("Timer '%s' (%s) merged from Home Assistant: %s", theirs.timer_id, theirs.name, result.status.value)
    return merged


def cleanup_timers(timers: Mapping[str, Timer], now: float, known_to_home_assistant: Optional[Set[str]]) -> Dict[str, Timer]:
    """
    Delete timers that are no longer needed.
    :param timers: The timers by timer id.
    :param now: Current time.
    :param known_to_home_assistant: Ids in the last timer list of Home Assistant, None if there is none.
    :return: The timers without ``finished`` or ``cancelled`` timers that ended more than 7 days ago, and without ``paused`` timers that
        Home Assistant does not know and that have not changed for 7 days. Ringing timers are kept.
    """
    kept = {}
    known = known_to_home_assistant or set()
    for timer_id, timer in timers.items():
        if timer.status in (TimerStatus.FINISHED, TimerStatus.CANCELLED) and not timer.ringing and now - (timer.ended_at or now) > ARCHIVE_SECONDS:
            _LOGGER.debug("Timer '%s' (%s) deleted, it ended %.0f s ago (limit %d s)", timer_id, timer.name, now - (timer.ended_at or now), ARCHIVE_SECONDS)
            continue
        if timer.status == TimerStatus.PAUSED and timer_id not in known and now - timer.changed_at > ARCHIVE_SECONDS:
            _LOGGER.info("Paused timer '%s' (%s) deleted, Home Assistant does not know it and it did not change for %.0f s", timer_id, timer.name, now - timer.changed_at)
            continue
        kept[timer_id] = timer
    return kept


def display_timer(timers: Mapping[str, Timer]) -> Optional[Timer]:
    """
    Choose the timer to show.
    :param timers: The timers by timer id.
    :return: The ringing timer that rings longest, else the ``active`` timer that ends first, else the ``paused`` timer with the least
        time left, else None.
    """
    ringing = ringing_timers(timers)
    if ringing:
        return timers[ringing[0]]
    active = [timer for timer in timers.values() if timer.status == TimerStatus.ACTIVE and timer.finishes_at is not None]
    if active:
        return min(active, key=lambda timer: timer.finishes_at or 0.0)
    paused = [timer for timer in timers.values() if timer.status == TimerStatus.PAUSED]
    if paused:
        return min(paused, key=lambda timer: timer.paused_remaining or 0.0)
    return None


def timers_to_dicts(timers: Mapping[str, Timer]) -> List[Dict[str, Any]]:
    """
    Convert the timers for ``preferences.json``.
    :param timers: The timers by timer id.
    """
    return [{**asdict(timer), "status": timer.status.value} for timer in timers.values()]


def timers_from_dicts(items: Iterable[Mapping[str, Any]]) -> Dict[str, Timer]:
    """
    Read the timers stored in ``preferences.json``.
    :param items: The stored timers.
    :return: The timers by timer id. An entry that cannot be read is skipped with a warning.
    """
    timers = {}
    for item in items:
        try:
            timer = Timer(**{**item, "status": TimerStatus(item["status"])})
        except (KeyError, TypeError, ValueError) as error:
            _LOGGER.warning("Ignoring stored timer that cannot be read (%s): %s", error, item)
            continue
        timers[timer.timer_id] = timer
    return timers


class TimerList:
    """
    Holds the current timers. Safe to use from several threads.

    Every method that changes the list returns True if it changed, so the caller knows when to store it.
    """

    def __init__(self, timers: Optional[Mapping[str, Timer]] = None) -> None:
        """
        :param timers: The timers to start with, by timer id.
        """
        self._timers: Dict[str, Timer] = dict(timers or {})
        self._lock = threading.Lock()

    @classmethod
    def from_dicts(cls, items: Iterable[Mapping[str, Any]]) -> "TimerList":
        """
        Build the list from the timers stored in ``preferences.json``.
        :param items: The stored timers.
        """
        return cls(timers_from_dicts(items))

    @property
    def timers(self) -> Dict[str, Timer]:
        """A copy of the timers by timer id."""
        with self._lock:
            return dict(self._timers)

    def to_dicts(self) -> List[Dict[str, Any]]:
        """The timers for ``preferences.json``."""
        return timers_to_dicts(self.timers)

    def apply_event(self, event: TimerEvent, now: float) -> bool:
        """
        Apply a timer event of Home Assistant, see ``apply_timer_event()``.
        :param event: The event.
        :param now: Current time.
        """
        with self._lock:
            return self._update(apply_timer_event(self._timers, event, now))

    def due_timers(self, now: float) -> Tuple[str, ...]:
        """
        Ids of the ``active`` timers whose end time has passed, the earliest first.
        :param now: Current time.
        """
        return due_timers(self.timers, now)

    def start_ringing(self, timer_ids: Iterable[str], now: float) -> bool:
        """
        Mark timers as ringing and ``finished``.
        :param timer_ids: The timers that start ringing.
        :param now: Current time.
        """
        with self._lock:
            return self._update(start_ringing(self._timers, timer_ids, now))

    def ringing_timers(self) -> Tuple[str, ...]:
        """Ids of the ringing timers, the one that rings longest first."""
        return ringing_timers(self.timers)

    def stop_ringing(self, now: float, timer_ids: Optional[Iterable[str]] = None) -> bool:
        """
        End the ringing of timers.
        :param now: Current time.
        :param timer_ids: The timers whose ringing ends. None ends the ringing of all timers.
        """
        with self._lock:
            ids = ringing_timers(self._timers) if timer_ids is None else timer_ids
            return self._update(stop_ringing(self._timers, ids, now))

    def auto_stopped_timers(self, now: float, max_ring_seconds: float) -> Tuple[str, ...]:
        """
        Ids of the ringing timers that started ringing at least ``max_ring_seconds`` ago.
        :param now: Current time.
        :param max_ring_seconds: How long a timer rings at most.
        """
        return auto_stopped_timers(self.timers, now, max_ring_seconds)

    def restart_ringing(self, now: float) -> bool:
        """
        Let the timers that rang before a restart of LVA ring again for the full time.
        :param now: Current time.
        """
        with self._lock:
            return self._update(restart_ringing(self._timers, now))

    def merge(self, remote: Iterable[Timer], now: float) -> bool:
        """
        Merge the timer list of Home Assistant, see ``merge_timers()``.
        :param remote: The timers Home Assistant reports.
        :param now: Current time.
        """
        with self._lock:
            return self._update(merge_timers(self._timers, remote, now))

    def cleanup(self, now: float, known_to_home_assistant: Optional[Set[str]]) -> bool:
        """
        Delete timers that are no longer needed, see ``cleanup_timers()``.
        :param now: Current time.
        :param known_to_home_assistant: Ids in the last timer list of Home Assistant, None if there is none.
        """
        with self._lock:
            return self._update(cleanup_timers(self._timers, now, known_to_home_assistant))

    def display_timer(self) -> Optional[Timer]:
        """The timer to show, see ``display_timer()``."""
        return display_timer(self.timers)

    def _update(self, timers: Dict[str, Timer]) -> bool:
        if timers == self._timers:
            return False
        self._timers = timers
        return True


def _active(event: TimerEvent, finishes_at: float, now: float) -> Timer:
    return Timer(event.timer_id, event.name, event.total_seconds, TimerStatus.ACTIVE, now, finishes_at=finishes_at)


def _cancelled(timer: Timer, now: float) -> Timer:
    return replace(timer, status=TimerStatus.CANCELLED, finishes_at=None, paused_remaining=None, ended_at=now, ringing_since=None, changed_at=now)


def _merged_timer(ours: Optional[Timer], theirs: Timer, now: float) -> Optional[Timer]:
    if ours is None:
        if theirs.status in (TimerStatus.ACTIVE, TimerStatus.PAUSED):
            return replace(theirs, ringing_since=None, changed_at=now)
        return None
    if _content(ours) == _content(theirs):
        return ours
    if theirs.status == TimerStatus.CANCELLED:
        return ours if ours.status == TimerStatus.CANCELLED else _cancelled(ours, now)
    if theirs.status == TimerStatus.FINISHED:
        if ours.status in (TimerStatus.ACTIVE, TimerStatus.PAUSED):
            return replace(ours, status=TimerStatus.ACTIVE, finishes_at=theirs.ended_at if theirs.ended_at is not None else now, paused_remaining=None, changed_at=now)
        return ours
    return replace(theirs, ringing_since=None, changed_at=now)


def _content(timer: Timer) -> Tuple[Any, ...]:
    return (timer.name, timer.total_seconds, timer.status, timer.finishes_at, timer.paused_remaining)
