"""Tests for the ringing of the timers (linux_voice_assistant/timer_alarm.py)."""

import asyncio
import json
import logging
from unittest.mock import MagicMock, call, patch

import pytest

from linux_voice_assistant.peripheral_api import LVAEvent
from linux_voice_assistant.timer_alarm import SOUND_PAUSE_SECONDS, TimerAlarm
from linux_voice_assistant.timers import Timer, TimerEvent, TimerList, TimerStatus

from .conftest import make_state
from .test_timers import CANCELLED, FINISHED, NOW, STARTED, UPDATED


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> float:
        return self.now


def _alarm(tmp_path, **state_overrides):
    clock = Clock()
    state = make_state(tmp_path, timer_max_ring_seconds=10.0, **state_overrides)
    state.peripheral_api = MagicMock()
    state.timer_alarm = TimerAlarm(state, clock=clock)
    return state, state.timer_alarm, clock


def _event(event_type, timer_id="t1", seconds_left=60, is_active=True):
    return TimerEvent(event_type, timer_id, "pasta", 60, seconds_left, is_active)


def _emitted(state):
    return [c.args for c in state.peripheral_api.emit_event_sync.call_args_list]


def _data(timer_id="t1", seconds_left=60):
    return {"id": timer_id, "name": "pasta", "total_seconds": 60, "seconds_left": seconds_left}


# ---------------------------------------------------------------- events


def test_started_reports_the_countdown_and_stores_the_list(tmp_path):
    state, alarm, _ = _alarm(tmp_path)
    alarm.handle_event(_event(STARTED))

    assert _emitted(state) == [(LVAEvent.TIMER_TICKING, _data())]
    stored = json.loads(state.preferences_path.read_text(encoding="utf-8"))
    assert [timer["timer_id"] for timer in stored["timers"]] == ["t1"]
    assert not alarm.ringing


def test_updated_reports_the_new_time(tmp_path):
    state, alarm, _ = _alarm(tmp_path)
    alarm.handle_event(_event(STARTED))
    alarm.handle_event(_event(UPDATED, seconds_left=30, is_active=False))

    assert _emitted(state)[-1] == (LVAEvent.TIMER_UPDATED, _data(seconds_left=30))
    assert state.timers.timers["t1"].status == TimerStatus.PAUSED


def test_finished_rings_at_once(tmp_path):
    state, alarm, _ = _alarm(tmp_path)
    alarm.handle_event(_event(FINISHED, seconds_left=0))

    assert alarm.ringing
    assert state.stop_word.id in state.active_wake_words
    state.music_player.duck.assert_called_once_with(owner="timer")
    state.tts_player.play.assert_called_once()
    assert state.tts_player.play.call_args.args == (state.timer_finished_sound,)
    assert _emitted(state) == [(LVAEvent.TIMER_RINGING, _data(seconds_left=0))]


def test_cancelled_reports_zero_and_idle_when_no_timer_is_left(tmp_path):
    state, alarm, _ = _alarm(tmp_path)
    alarm.handle_event(_event(STARTED))
    alarm.handle_event(_event(CANCELLED, seconds_left=42))

    assert _emitted(state)[1:] == [(LVAEvent.TIMER_UPDATED, _data(seconds_left=0)), (LVAEvent.IDLE, None)]


def test_cancelled_shows_the_timer_that_still_runs(tmp_path, caplog):
    state, alarm, clock = _alarm(tmp_path)
    alarm.handle_event(_event(STARTED, timer_id="t1"))
    alarm.handle_event(_event(STARTED, timer_id="t2", seconds_left=30))
    clock.now += 10
    with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.timer_alarm"):
        alarm.handle_event(_event(CANCELLED, timer_id="t1"))

    assert _emitted(state)[2:] == [(LVAEvent.TIMER_UPDATED, _data(seconds_left=0)), (LVAEvent.TIMER_TICKING, _data("t2", seconds_left=20))]
    assert "Timer 't1' cancelled, timer 't2' still runs" in caplog.text


def test_cancelling_the_ringing_timer_stops_the_ringing(tmp_path):
    state, alarm, _ = _alarm(tmp_path)
    alarm.handle_event(_event(FINISHED, seconds_left=0))
    alarm.handle_event(_event(CANCELLED))

    assert not alarm.ringing
    state.music_player.unduck.assert_called_once_with(owner="timer")
    assert _emitted(state)[1:] == [(LVAEvent.TIMER_UPDATED, _data(seconds_left=0)), (LVAEvent.IDLE, None)]


def test_cancelling_a_timer_while_another_rings_keeps_the_ringing(tmp_path):
    state, alarm, _ = _alarm(tmp_path)
    alarm.handle_event(_event(STARTED, timer_id="t2"))
    alarm.handle_event(_event(FINISHED, seconds_left=0))
    alarm.handle_event(_event(CANCELLED, timer_id="t2"))

    assert alarm.ringing
    assert _emitted(state)[-1] == (LVAEvent.TIMER_UPDATED, _data("t2", seconds_left=0))


# ---------------------------------------------------------------- countdown


def test_countdown_rings_a_timer_when_its_time_is_up(tmp_path):
    state, alarm, clock = _alarm(tmp_path)
    alarm.handle_event(_event(STARTED, seconds_left=10))
    clock.now += 9
    alarm.tick()
    assert not alarm.ringing

    clock.now += 1
    alarm.tick()
    assert alarm.ringing
    assert _emitted(state)[-1] == (LVAEvent.TIMER_RINGING, _data(seconds_left=0))


def test_a_second_timer_rings_with_the_same_sound(tmp_path):
    state, alarm, _ = _alarm(tmp_path)
    alarm.handle_event(_event(FINISHED, timer_id="t1", seconds_left=0))
    alarm.handle_event(_event(FINISHED, timer_id="t2", seconds_left=0))

    state.tts_player.play.assert_called_once()
    state.music_player.duck.assert_called_once_with(owner="timer")
    assert [event for event, _ in _emitted(state)] == [LVAEvent.TIMER_RINGING, LVAEvent.TIMER_RINGING]


def test_auto_stop_reports_how_long_it_rang_then_idle(tmp_path, caplog):
    state, alarm, clock = _alarm(tmp_path)
    alarm.handle_event(_event(FINISHED, seconds_left=0))
    clock.now += 10
    with caplog.at_level(logging.INFO, logger="linux_voice_assistant.timer_alarm"):
        alarm.tick()

    assert not alarm.ringing
    assert _emitted(state)[1:] == [(LVAEvent.TIMER_AUTO_STOPPED, {"seconds": 10}), (LVAEvent.IDLE, None)]
    assert "Timer auto-stopped after 10 seconds (max=10)" in caplog.text
    assert state.stop_word.id not in state.active_wake_words
    state.music_player.unduck.assert_called_once_with(owner="timer")


def test_auto_stop_counts_for_each_timer(tmp_path):
    state, alarm, clock = _alarm(tmp_path)
    alarm.handle_event(_event(FINISHED, timer_id="t1", seconds_left=0))
    clock.now += 5
    alarm.handle_event(_event(FINISHED, timer_id="t2", seconds_left=0))
    clock.now += 5
    alarm.tick()

    assert alarm.ringing
    assert _emitted(state)[-1] == (LVAEvent.TIMER_AUTO_STOPPED, {"seconds": 10})


def test_ringing_goes_on_before_the_limit(tmp_path):
    state, alarm, clock = _alarm(tmp_path)
    alarm.handle_event(_event(FINISHED, seconds_left=0))
    clock.now += 9
    alarm.tick()

    assert alarm.ringing
    assert len(_emitted(state)) == 1


def test_a_finished_timer_does_not_ring_again_on_a_new_finished_event(tmp_path):
    _, alarm, _ = _alarm(tmp_path)
    alarm.handle_event(_event(FINISHED, seconds_left=0))
    alarm.stop_ringing()
    alarm.handle_event(_event(FINISHED, seconds_left=0))

    assert not alarm.ringing


# ---------------------------------------------------------------- stop


def test_stop_ringing_restores_everything(tmp_path, caplog):
    state, alarm, _ = _alarm(tmp_path)
    alarm.handle_event(_event(FINISHED, seconds_left=0))
    with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.timer_alarm"):
        assert alarm.stop_ringing()

    assert not alarm.ringing
    assert state.stop_word.id not in state.active_wake_words
    state.tts_player.stop.assert_called_once()
    state.music_player.unduck.assert_called_once_with(owner="timer")
    assert _emitted(state)[-1] == (LVAEvent.IDLE, None)
    assert "Stopping timer finished sound" in caplog.text
    assert state.timers.timers["t1"].status == TimerStatus.FINISHED
    assert not state.timers.timers["t1"].ringing


def test_stop_ringing_without_idle(tmp_path):
    state, alarm, _ = _alarm(tmp_path)
    alarm.handle_event(_event(FINISHED, seconds_left=0))
    assert alarm.stop_ringing(report_idle=False)
    assert (LVAEvent.IDLE, None) not in _emitted(state)


def test_stop_ringing_without_a_ringing_timer_does_nothing(tmp_path):
    state, alarm, _ = _alarm(tmp_path)
    assert not alarm.stop_ringing()
    state.tts_player.stop.assert_not_called()
    state.music_player.unduck.assert_not_called()


# ---------------------------------------------------------------- sound


def test_sound_repeats_after_a_pause(tmp_path):
    state, alarm, _ = _alarm(tmp_path)
    alarm.handle_event(_event(FINISHED, seconds_left=0))
    with patch("linux_voice_assistant.timer_alarm.threading.Timer") as timer:
        state.tts_player.play.call_args.kwargs["done_callback"]()
    assert timer.call_args.args[0] == SOUND_PAUSE_SECONDS

    repeat, args = timer.call_args.args[1], timer.call_args.kwargs["args"]
    repeat(*args)
    assert state.tts_player.play.call_count == 2


def test_pending_repeat_does_not_play_after_the_ringing_ended(tmp_path):
    state, alarm, _ = _alarm(tmp_path)
    alarm.handle_event(_event(FINISHED, seconds_left=0))
    with patch("linux_voice_assistant.timer_alarm.threading.Timer") as timer:
        state.tts_player.play.call_args.kwargs["done_callback"]()
    alarm.stop_ringing()
    alarm.handle_event(_event(FINISHED, timer_id="t2", seconds_left=0))
    assert state.tts_player.play.call_count == 2

    repeat, args = timer.call_args.args[1], timer.call_args.kwargs["args"]
    repeat(*args)
    assert state.tts_player.play.call_count == 2


# ---------------------------------------------------------------- restart


def test_restore_rings_a_timer_that_rang_before_the_restart(tmp_path):
    timers = {"t1": Timer("t1", "pasta", 60, TimerStatus.FINISHED, NOW - 100, ended_at=NOW - 100, ringing_since=NOW - 100)}
    state, alarm, _ = _alarm(tmp_path, timers=TimerList(timers))
    alarm.restore()

    assert alarm.ringing
    assert state.timers.timers["t1"].ringing_since == NOW
    assert _emitted(state) == [(LVAEvent.TIMER_RINGING, _data(seconds_left=0))]


def test_restore_without_a_ringing_timer_does_nothing(tmp_path):
    state, alarm, _ = _alarm(tmp_path, timers=TimerList({"t1": Timer("t1", "pasta", 60, TimerStatus.ACTIVE, NOW, finishes_at=NOW + 50)}))
    alarm.restore()

    assert not alarm.ringing
    state.tts_player.play.assert_not_called()


async def test_countdown_restores_then_rings_overdue_timers(tmp_path):
    state, alarm, _ = _alarm(tmp_path, timers=TimerList({"t1": Timer("t1", "pasta", 60, TimerStatus.ACTIVE, NOW - 100, finishes_at=NOW - 30)}))
    with patch("linux_voice_assistant.timer_alarm.asyncio.sleep", side_effect=asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            await alarm.run_countdown()

    assert alarm.ringing
    assert call(LVAEvent.TIMER_RINGING, _data(seconds_left=0)) in state.peripheral_api.emit_event_sync.call_args_list


async def test_countdown_goes_on_after_an_error(tmp_path, caplog):
    _, alarm, _ = _alarm(tmp_path)
    with (
        patch.object(alarm, "tick", side_effect=[RuntimeError("boom"), None]) as tick,
        patch("linux_voice_assistant.timer_alarm.asyncio.sleep", side_effect=[None, asyncio.CancelledError]),
        caplog.at_level(logging.ERROR, logger="linux_voice_assistant.timer_alarm"),
    ):
        with pytest.raises(asyncio.CancelledError):
            await alarm.run_countdown()

    assert tick.call_count == 2
    assert "Unexpected error in the timer countdown" in caplog.text


def test_without_peripheral_api(tmp_path):
    state, alarm, _ = _alarm(tmp_path)
    state.peripheral_api = None
    alarm.handle_event(_event(FINISHED, seconds_left=0))
    assert alarm.stop_ringing()
