"""Tests for the timer list (linux_voice_assistant/timers.py)."""

import json
import logging
import threading

from aioesphomeapi.model import VoiceAssistantTimerEventType

from linux_voice_assistant.models import Preferences
from linux_voice_assistant.timers import (
    ARCHIVE_SECONDS,
    Timer,
    TimerEvent,
    TimerList,
    TimerStatus,
    apply_timer_event,
    auto_stopped_timers,
    cleanup_timers,
    display_timer,
    due_timers,
    merge_timers,
    restart_ringing,
    ringing_timers,
    start_ringing,
    stop_ringing,
    timers_from_dicts,
    timers_to_dicts,
)

from .conftest import make_state

STARTED = VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_STARTED
UPDATED = VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_UPDATED
CANCELLED = VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_CANCELLED
FINISHED = VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_FINISHED

NOW = 1_000_000.0


def _event(event_type, *, timer_id="t1", seconds_left=60, is_active=True):
    return TimerEvent(event_type, timer_id, "pasta", 60, seconds_left, is_active)


def _active(timer_id="t1", finishes_at=NOW + 60, name="pasta", total_seconds=60, changed_at=NOW):
    return Timer(timer_id, name, total_seconds, TimerStatus.ACTIVE, changed_at, finishes_at=finishes_at)


def _paused(timer_id="t1", remaining=30.0, changed_at=NOW, name="pasta", total_seconds=60):
    return Timer(timer_id, name, total_seconds, TimerStatus.PAUSED, changed_at, paused_remaining=remaining)


def _finished(timer_id="t1", ended_at=NOW, ringing_since=None):
    return Timer(timer_id, "pasta", 60, TimerStatus.FINISHED, ended_at, ended_at=ended_at, ringing_since=ringing_since)


def _cancelled(timer_id="t1", ended_at=NOW):
    return Timer(timer_id, "pasta", 60, TimerStatus.CANCELLED, ended_at, ended_at=ended_at)


# ---------------------------------------------------------------- events


def test_started_adds_an_active_timer():
    timers = apply_timer_event({}, _event(STARTED, seconds_left=60), NOW)
    assert timers == {"t1": _active(finishes_at=NOW + 60)}


def test_started_is_logged(caplog):
    with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.timers"):
        apply_timer_event({}, _event(STARTED, seconds_left=60), NOW)
    assert "Timer 't1' (pasta) started, finishes in 60 s" in caplog.text


def test_updated_running_sets_the_end_time():
    timers = apply_timer_event({"t1": _active()}, _event(UPDATED, seconds_left=120), NOW + 10)
    assert timers["t1"].finishes_at == NOW + 130
    assert timers["t1"].status == TimerStatus.ACTIVE


def test_updated_adds_an_unknown_timer():
    timers = apply_timer_event({}, _event(UPDATED, seconds_left=40), NOW)
    assert timers["t1"].status == TimerStatus.ACTIVE
    assert timers["t1"].finishes_at == NOW + 40


def test_updated_not_active_pauses_the_timer():
    timers = apply_timer_event({"t1": _active()}, _event(UPDATED, seconds_left=25, is_active=False), NOW)
    assert timers["t1"] == _paused(remaining=25.0)
    assert timers["t1"].finishes_at is None


def test_resume_after_pause_counts_from_now():
    timers = apply_timer_event({"t1": _paused(remaining=25.0)}, _event(UPDATED, seconds_left=25), NOW + 500)
    assert timers["t1"].status == TimerStatus.ACTIVE
    assert timers["t1"].finishes_at == NOW + 525
    assert timers["t1"].paused_remaining is None


def test_cancelled_cancels_only_that_timer():
    timers = {"t1": _active(), "t2": _active("t2")}
    updated = apply_timer_event(timers, _event(CANCELLED), NOW + 5)
    assert updated["t1"].status == TimerStatus.CANCELLED
    assert updated["t1"].ended_at == NOW + 5
    assert updated["t1"].finishes_at is None
    assert updated["t2"] == timers["t2"]


def test_cancelled_stops_the_ringing_of_that_timer():
    timers = {"t1": _finished(ringing_since=NOW), "t2": _finished("t2", ringing_since=NOW)}
    updated = apply_timer_event(timers, _event(CANCELLED), NOW + 5)
    assert not updated["t1"].ringing
    assert updated["t2"].ringing


def test_cancelled_unknown_timer_is_ignored(caplog):
    with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.timers"):
        assert not apply_timer_event({}, _event(CANCELLED), NOW)
    assert "Timer 't1' cancelled, but it is not in the list" in caplog.text


def test_finished_makes_an_active_timer_due_now():
    timers = apply_timer_event({"t1": _active(finishes_at=NOW + 3)}, _event(FINISHED, seconds_left=0), NOW)
    assert timers["t1"].finishes_at == NOW
    assert due_timers(timers, NOW) == ("t1",)


def test_finished_adds_an_unknown_timer_that_is_due_now():
    timers = apply_timer_event({}, _event(FINISHED, seconds_left=0), NOW)
    assert due_timers(timers, NOW) == ("t1",)


def test_finished_does_nothing_for_a_ringing_timer(caplog):
    timers = {"t1": _finished(ringing_since=NOW)}
    with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.timers"):
        assert apply_timer_event(timers, _event(FINISHED), NOW + 1) == timers
    assert "Timer 't1' (pasta) finished, but it rings already" in caplog.text


def test_finished_does_nothing_for_a_timer_that_finished_already(caplog):
    timers = {"t1": _finished()}
    with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.timers"):
        assert apply_timer_event(timers, _event(FINISHED), NOW + 1) == timers
    assert due_timers(timers, NOW + 1) == ()
    assert "Timer 't1' (pasta) finished, but it finished already, it does not ring again" in caplog.text


# ---------------------------------------------------------------- countdown and ringing


def test_due_timers_are_the_active_ones_that_passed_their_end_earliest_first():
    timers = {"t1": _active(finishes_at=NOW - 1), "t2": _active("t2", finishes_at=NOW - 5), "t3": _active("t3", finishes_at=NOW + 1), "t4": _paused("t4")}
    assert due_timers(timers, NOW) == ("t2", "t1")


def test_paused_timer_is_never_due():
    assert not due_timers({"t1": _paused()}, NOW + 10 * ARCHIVE_SECONDS)


def test_start_ringing_sets_finished_and_ringing_since():
    timers = start_ringing({"t1": _active(finishes_at=NOW)}, ["t1", "unknown"], NOW + 1)
    assert timers["t1"].status == TimerStatus.FINISHED
    assert timers["t1"].ringing_since == NOW + 1
    assert timers["t1"].ended_at == NOW + 1
    assert timers["t1"].finishes_at is None
    assert "unknown" not in timers
    assert not due_timers(timers, NOW + 2)


def test_ringing_timers_longest_ringing_first():
    timers = {"t1": _finished(ringing_since=NOW + 2), "t2": _finished("t2", ringing_since=NOW), "t3": _finished("t3")}
    assert ringing_timers(timers) == ("t2", "t1")


def test_stop_ringing_keeps_the_timer_finished(caplog):
    with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.timers"):
        timers = stop_ringing({"t1": _finished(ringing_since=NOW)}, ["t1"], NOW + 4)
    assert timers["t1"].status == TimerStatus.FINISHED
    assert not timers["t1"].ringing
    assert "Timer 't1' (pasta) stops ringing after 4.0 s" in caplog.text


def test_stop_ringing_skips_timers_that_do_not_ring():
    timers = {"t1": _active()}
    assert stop_ringing(timers, ["t1", "unknown"], NOW) == timers


def test_auto_stopped_timers_counts_from_the_start_of_the_ringing(caplog):
    timers = {"t1": _finished(ringing_since=NOW), "t2": _finished("t2", ringing_since=NOW + 5)}
    with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.timers"):
        assert auto_stopped_timers(timers, NOW + 10, max_ring_seconds=10) == ("t1",)
    assert "Timer 't1' rang for 10.0 s, stopping it on its own (limit 10.0 s)" in caplog.text


def test_restart_ringing_rings_again_for_the_full_time():
    timers = restart_ringing({"t1": _finished(ringing_since=NOW), "t2": _finished("t2")}, NOW + 100)
    assert timers["t1"].ringing_since == NOW + 100
    assert not timers["t2"].ringing
    assert not auto_stopped_timers(timers, NOW + 100, max_ring_seconds=10)


# ---------------------------------------------------------------- merge with Home Assistant


def test_merge_takes_over_active_or_paused_timer_unknown_locally():
    merged = merge_timers({}, [_active(changed_at=0.0), _paused("t2", changed_at=0.0)], NOW)
    assert merged["t1"] == _active()
    assert merged["t2"] == _paused("t2")


def test_merge_takes_the_values_of_home_assistant_when_they_differ():
    merged = merge_timers({"t1": _active(finishes_at=NOW + 60)}, [_paused(remaining=12.0)], NOW + 1)
    assert merged["t1"] == _paused(remaining=12.0, changed_at=NOW + 1)


def test_merge_finished_in_home_assistant_makes_a_running_timer_due():
    merged = merge_timers({"t1": _active(finishes_at=NOW + 60)}, [_finished(ended_at=NOW - 3)], NOW)
    assert merged["t1"].status == TimerStatus.ACTIVE
    assert merged["t1"].finishes_at == NOW - 3
    assert due_timers(merged, NOW) == ("t1",)


def test_merge_finished_in_home_assistant_makes_a_paused_timer_due():
    merged = merge_timers({"t1": _paused()}, [_finished(ended_at=NOW - 3)], NOW)
    assert due_timers(merged, NOW) == ("t1",)


def test_merge_cancelled_in_home_assistant_cancels_and_stops_ringing():
    merged = merge_timers({"t1": _finished(ringing_since=NOW)}, [_cancelled()], NOW + 1)
    assert merged["t1"].status == TimerStatus.CANCELLED
    assert not merged["t1"].ringing


def test_merge_ignores_ended_timers_unknown_locally():
    assert not merge_timers({}, [_finished(), _cancelled("t2")], NOW)


def test_merge_keeps_equal_timers_unchanged():
    timers = {"t1": _active(changed_at=NOW - 50)}
    assert merge_timers(timers, [_active(changed_at=NOW)], NOW) == timers


def test_merge_keeps_local_timers_missing_in_home_assistant():
    timers = {"t1": _active()}
    assert merge_timers(timers, [], NOW) == timers


def test_merge_is_logged(caplog):
    with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.timers"):
        merge_timers({}, [_active()], NOW)
    assert "Timer 't1' (pasta) merged from Home Assistant: active" in caplog.text


# ---------------------------------------------------------------- cleanup


def test_cleanup_deletes_ended_timers_after_seven_days(caplog):
    timers = {"old": _finished("old", ended_at=NOW - ARCHIVE_SECONDS - 1), "new": _cancelled("new", ended_at=NOW - ARCHIVE_SECONDS + 1)}
    with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.timers"):
        assert set(cleanup_timers(timers, NOW, None)) == {"new"}
    assert "Timer 'old' (pasta) deleted, it ended" in caplog.text


def test_cleanup_keeps_ringing_timers():
    timers = {"t1": _finished(ended_at=NOW - ARCHIVE_SECONDS - 1, ringing_since=NOW)}
    assert cleanup_timers(timers, NOW, None) == timers


def test_cleanup_deletes_paused_timers_unknown_to_home_assistant(caplog):
    timers = {"t1": _paused(changed_at=NOW - ARCHIVE_SECONDS - 1)}
    with caplog.at_level(logging.INFO, logger="linux_voice_assistant.timers"):
        assert not cleanup_timers(timers, NOW, {"other"})
    assert "Paused timer 't1' (pasta) deleted, Home Assistant does not know it" in caplog.text


def test_cleanup_keeps_paused_timers_known_to_home_assistant():
    timers = {"t1": _paused(changed_at=NOW - ARCHIVE_SECONDS - 1)}
    assert cleanup_timers(timers, NOW, {"t1"}) == timers


def test_cleanup_keeps_recent_paused_timers_without_a_list_of_home_assistant():
    timers = {"t1": _paused(changed_at=NOW - 10)}
    assert cleanup_timers(timers, NOW, None) == timers


# ---------------------------------------------------------------- display


def test_display_prefers_a_ringing_timer():
    timers = {"t1": _active(finishes_at=NOW + 1), "t2": _finished("t2", ringing_since=NOW)}
    assert display_timer(timers).timer_id == "t2"


def test_display_takes_the_active_timer_that_ends_first():
    timers = {"t1": _active(finishes_at=NOW + 60), "t2": _active("t2", finishes_at=NOW + 10), "t3": _paused("t3", remaining=1.0)}
    assert display_timer(timers).timer_id == "t2"


def test_display_takes_the_paused_timer_with_the_least_time_left():
    timers = {"t1": _paused(remaining=30.0), "t2": _paused("t2", remaining=5.0), "t3": _cancelled("t3")}
    assert display_timer(timers).timer_id == "t2"


def test_display_is_none_without_a_running_timer():
    assert display_timer({"t1": _finished(), "t2": _cancelled("t2")}) is None


# ---------------------------------------------------------------- storage


def test_round_trip_through_dicts_and_json():
    timers = {"t1": _active(), "t2": _paused("t2"), "t3": _finished("t3", ringing_since=NOW)}
    assert timers_from_dicts(json.loads(json.dumps(timers_to_dicts(timers)))) == timers


def test_stored_status_is_the_spec_value():
    assert timers_to_dicts({"t1": _paused()})[0]["status"] == "paused"


def test_unreadable_stored_timer_is_skipped(caplog):
    with caplog.at_level(logging.WARNING, logger="linux_voice_assistant.timers"):
        timers = timers_from_dicts([{"timer_id": "t1"}, {**timers_to_dicts({"t2": _active("t2")})[0]}, {**timers_to_dicts({"t3": _active("t3")})[0], "status": "ringing"}])
    assert set(timers) == {"t2"}
    assert caplog.text.count("Ignoring stored timer that cannot be read") == 2


def test_preferences_default_to_an_empty_timer_list():
    assert not Preferences().timers
    assert Preferences.from_dict({"timers": [{"timer_id": "t1"}]}).timers == [{"timer_id": "t1"}]


def test_persist_timers_stores_the_list(tmp_path):
    state = make_state(tmp_path, preferences_path=tmp_path / "preferences.json")
    state.timers.apply_event(_event(STARTED), NOW)
    state.persist_timers()
    stored = json.loads((tmp_path / "preferences.json").read_text(encoding="utf-8"))
    assert TimerList.from_dicts(stored["timers"]).timers == state.timers.timers


# ---------------------------------------------------------------- TimerList


def test_timer_list_reports_whether_it_changed():
    timer_list = TimerList()
    assert timer_list.apply_event(_event(STARTED), NOW)
    assert not timer_list.apply_event(_event(STARTED), NOW)
    assert not timer_list.apply_event(_event(CANCELLED, timer_id="unknown"), NOW)


def test_timer_list_life_of_a_timer():
    timer_list = TimerList()
    timer_list.apply_event(_event(STARTED, seconds_left=10), NOW)
    assert not timer_list.due_timers(NOW + 9)
    assert timer_list.due_timers(NOW + 10) == ("t1",)

    assert timer_list.start_ringing(timer_list.due_timers(NOW + 10), NOW + 10)
    assert timer_list.ringing_timers() == ("t1",)
    assert timer_list.display_timer().timer_id == "t1"
    assert timer_list.auto_stopped_timers(NOW + 20, max_ring_seconds=10) == ("t1",)

    assert timer_list.stop_ringing(NOW + 20, timer_list.auto_stopped_timers(NOW + 20, max_ring_seconds=10))
    assert not timer_list.ringing_timers()
    assert timer_list.display_timer() is None

    assert timer_list.cleanup(NOW + 20 + ARCHIVE_SECONDS + 1, None)
    assert not timer_list.timers


def test_timer_list_stop_ringing_without_ids_stops_all():
    timer_list = TimerList({"t1": _finished(ringing_since=NOW), "t2": _finished("t2", ringing_since=NOW)})
    assert timer_list.stop_ringing(NOW + 1)
    assert not timer_list.ringing_timers()
    assert not timer_list.stop_ringing(NOW + 2)


def test_timer_list_restart_and_merge():
    timer_list = TimerList.from_dicts(timers_to_dicts({"t1": _finished(ringing_since=NOW)}))
    assert timer_list.restart_ringing(NOW + 50)
    assert timer_list.timers["t1"].ringing_since == NOW + 50
    assert timer_list.merge([_active("t2")], NOW + 50)
    assert set(timer_list.timers) == {"t1", "t2"}


def test_timer_list_timers_is_a_copy():
    timer_list = TimerList({"t1": _active()})
    timer_list.timers.clear()
    assert timer_list.timers


def test_timer_list_from_several_threads():
    timer_list = TimerList()
    threads = [threading.Thread(target=timer_list.apply_event, args=(_event(STARTED, timer_id=f"t{index}"), NOW)) for index in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(timer_list.timers) == 20
