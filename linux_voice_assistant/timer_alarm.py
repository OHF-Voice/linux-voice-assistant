"""Ringing of the timers in the timer list.

``timers.py`` decides which timers are due and how long they ring. This module carries it out: it plays the timer sound on repeat, ducks
the music, makes the stop word active, sends the timer events of the peripheral API and stores the list. It lives in ``ServerState``, so
timers count down and ring whether Home Assistant is connected or not.
"""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from aioesphomeapi.model import VoiceAssistantTimerEventType

from .peripheral_api import LVAEvent
from .timers import Timer, TimerEvent, TimerStatus

if TYPE_CHECKING:
    from .models import ServerState

_LOGGER = logging.getLogger(__name__)

COUNTDOWN_INTERVAL_SECONDS = 1.0
SOUND_PAUSE_SECONDS = 1.0


class TimerAlarm:
    """
    Rings the timers of ``state.timers``. Safe to use from several threads.

    ``run_countdown()`` checks the list once a second. Events of Home Assistant go through ``handle_event()``, everything that stops a
    ringing timer (stop word, wake word, buttons, peripherals) calls ``stop_ringing()``.
    """

    def __init__(self, state: "ServerState", clock: Optional[Callable[[], float]] = None) -> None:
        """
        :param state: The server state with the timer list, the players and the peripheral API.
        :param clock: Wall clock in seconds that the times in the timer list are measured on. None uses the time of Home Assistant: the
            own clock plus ``state.home_assistant_time_offset``.
        """
        self._state = state
        self._clock = clock or (lambda: time.time() + state.home_assistant_time_offset)
        self._lock = threading.RLock()
        self._ringing = False
        # Each ringing gets its own round, so a sound repeat that is still pending from an earlier ringing does not play.
        self._round = 0

    @property
    def ringing(self) -> bool:
        """True while at least one timer rings."""
        return self._ringing

    def now(self) -> float:
        """Current time on the clock of the timer list."""
        return self._clock()

    def handle_event(self, event: TimerEvent) -> None:
        """
        Apply a timer event of Home Assistant and tell the peripherals.
        :param event: The event.
        """
        with self._lock:
            now = self.now()
            if self._state.timers.apply_event(event, now):
                self._state.persist_timers()
            self._update(now, report_idle=False)
            self._report_event(event, now)

    def merge(self, remote: List[Timer]) -> None:
        """
        Merge the timer list of Home Assistant, ring the timers that became due and tell the peripherals.
        :param remote: The timers Home Assistant reports.
        """
        with self._lock:
            now = self.now()
            changed = self._state.timers.merge(remote, now)
            if changed:
                self._state.persist_timers()
            self._update(now, report_idle=True)
            if not changed:
                return
            shown = self._state.timers.display_timer()
            if shown is not None and not self._ringing:
                self._emit(LVAEvent.TIMER_TICKING, _event_data(shown, now))
            api = self._state.peripheral_api
            if api is not None:
                api.refresh_status_sync()

    def tick(self) -> None:
        """Ring the timers whose time is up, stop the ones that rang long enough and delete old ones."""
        with self._lock:
            self._update(self.now(), report_idle=True)

    def stop_ringing(self, report_idle: bool = True) -> bool:
        """
        Stop all ringing timers. A timer that finishes later rings again.
        :param report_idle: Send ``idle`` to the peripherals. False if the caller goes on with an interaction of its own.
        :return: True if a timer rang.
        """
        with self._lock:
            if not self._ringing:
                return False
            if self._state.timers.stop_ringing(self.now()):
                self._state.persist_timers()
            self._end_ringing(report_idle)
            return True

    def restore(self) -> None:
        """After a start of LVA, let the timers that rang before ring again for the full time."""
        with self._lock:
            now = self.now()
            if not self._state.timers.restart_ringing(now):
                return
            self._state.persist_timers()
            for timer_id in self._state.timers.ringing_timers():
                self._emit(LVAEvent.TIMER_RINGING, _event_data(self._state.timers.timers[timer_id], now))
            self._start_ringing()

    async def run_countdown(self) -> None:
        """Restore the ringing of a previous run, then check the timer list once a second until cancelled."""
        self.restore()
        while True:
            try:
                self.tick()
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected error in the timer countdown")
            await asyncio.sleep(COUNTDOWN_INTERVAL_SECONDS)

    def _update(self, now: float, report_idle: bool) -> None:
        timers = self._state.timers
        changed = False

        due = timers.due_timers(now)
        if due:
            changed |= timers.start_ringing(due, now)
            for timer_id in due:
                self._emit(LVAEvent.TIMER_RINGING, _event_data(timers.timers[timer_id], now))

        auto_stopped = timers.auto_stopped_timers(now, self._state.timer_max_ring_seconds)
        if auto_stopped:
            rung = {timer_id: now - (timers.timers[timer_id].ringing_since or now) for timer_id in auto_stopped}
            changed |= timers.stop_ringing(now, auto_stopped)
            for timer_id, seconds in rung.items():
                _LOGGER.info("Timer auto-stopped after %.0f seconds (max=%.0f)", seconds, self._state.timer_max_ring_seconds)
                self._emit(LVAEvent.TIMER_AUTO_STOPPED, {"seconds": round(seconds)})

        changed |= timers.cleanup(now, self._state.timer_sync.known_timer_ids)
        if changed:
            self._state.persist_timers()

        ringing = bool(timers.ringing_timers())
        if ringing and not self._ringing:
            self._start_ringing()
        elif not ringing and self._ringing:
            self._end_ringing(report_idle=report_idle or bool(auto_stopped))

    def _report_event(self, event: TimerEvent, now: float) -> None:
        data = {"id": event.timer_id, "name": event.name, "total_seconds": event.total_seconds, "seconds_left": event.seconds_left}
        if event.event_type == VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_STARTED:
            self._emit(LVAEvent.TIMER_TICKING, data)
        elif event.event_type == VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_UPDATED:
            self._emit(LVAEvent.TIMER_UPDATED, data)
        elif event.event_type == VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_CANCELLED:
            # Report the cancel as a zeroed update. Every shipped peripheral already reads `seconds_left` from `timer_updated`, and none of
            # them treats zero as "ringing", that is `timer_ringing`, so the countdown clears without any peripheral having to learn a
            # new event.
            self._emit(LVAEvent.TIMER_UPDATED, {**data, "seconds_left": 0})
            if self._ringing:
                return
            shown = self._state.timers.display_timer()
            if shown is None:
                self._emit(LVAEvent.IDLE)
            else:
                _LOGGER.debug("Timer '%s' cancelled, timer '%s' still runs", event.timer_id, shown.timer_id)
                self._emit(LVAEvent.TIMER_TICKING, _event_data(shown, now))

    def _start_ringing(self) -> None:
        _LOGGER.debug("Starting timer finished sound")
        self._ringing = True
        self._round += 1
        self._state.active_wake_words.add(self._state.stop_word.id)
        self._state.music_player.duck(owner="timer")
        self._play_sound(self._round)

    def _end_ringing(self, report_idle: bool) -> None:
        _LOGGER.debug("Stopping timer finished sound")
        self._ringing = False
        self._round += 1
        self._state.active_wake_words.discard(self._state.stop_word.id)
        self._state.tts_player.stop()
        self._state.music_player.unduck(owner="timer")
        if report_idle:
            self._emit(LVAEvent.IDLE)

    def _play_sound(self, sound_round: int) -> None:
        with self._lock:
            if not self._ringing or sound_round != self._round:
                return
            self._state.tts_player.play(self._state.timer_finished_sound, done_callback=lambda: self._repeat_sound(sound_round))

    def _repeat_sound(self, sound_round: int) -> None:
        # Runs on a thread of the player or the caller of stop(), so the pause must not block it.
        repeat = threading.Timer(SOUND_PAUSE_SECONDS, self._play_sound, args=(sound_round,))
        repeat.daemon = True
        repeat.start()

    def _emit(self, event: LVAEvent, data: Optional[Dict[str, Any]] = None) -> None:
        api = self._state.peripheral_api
        if api is not None:
            api.emit_event_sync(event, data)


def _event_data(timer: Timer, now: float) -> Dict[str, Any]:
    if timer.status == TimerStatus.ACTIVE and timer.finishes_at is not None:
        seconds_left = max(0, math.ceil(timer.finishes_at - now))
    elif timer.status == TimerStatus.PAUSED and timer.paused_remaining is not None:
        seconds_left = math.ceil(timer.paused_remaining)
    else:
        seconds_left = 0
    return {"id": timer.timer_id, "name": timer.name, "total_seconds": timer.total_seconds, "seconds_left": seconds_left}
