"""Tests for the timers of version 2 of the peripheral API (peripheral_api/v2/timers.py)."""

from linux_voice_assistant.peripheral_api.v2.timers import TimerEntry, TimerSummary, has_running_timer, summarize_timers
from linux_voice_assistant.timers import Timer, TimerStatus

NOW = 1000.0


def _active(timer_id="t1", ends_in=30.0, total=60):
    return Timer(timer_id, "pasta", total, TimerStatus.ACTIVE, NOW, finishes_at=NOW + ends_in)


def _paused(timer_id="t1", remaining=15.0, total=60):
    return Timer(timer_id, "pasta", total, TimerStatus.PAUSED, NOW, paused_remaining=remaining)


def _ringing(timer_id="t1", since=NOW):
    return Timer(timer_id, "pasta", 60, TimerStatus.FINISHED, since, ended_at=since, ringing_since=since)


def _by_id(*timers):
    return {timer.timer_id: timer for timer in timers}


def test_no_timer_is_idle():
    assert summarize_timers({}, NOW) == TimerSummary("idle", 0, 0)


def test_active_timer_shows_its_time_left():
    summary = summarize_timers(_by_id(_active(ends_in=30.0)), NOW)
    assert summary == TimerSummary("active", 30, 50, (TimerEntry("t1", "pasta", "active", 60, 30, 50),))


def test_time_left_is_rounded_up():
    assert summarize_timers(_by_id(_active(ends_in=29.2)), NOW).remaining_time == 30


def test_paused_timer_is_reported_as_paused():
    summary = summarize_timers(_by_id(_paused(remaining=15.0)), NOW)
    assert summary == TimerSummary("paused", 15, 25, (TimerEntry("t1", "pasta", "paused", 60, 15, 25),))


def test_paused_time_left_does_not_change():
    timers = _by_id(_paused(remaining=15.0))
    assert summarize_timers(timers, NOW + 100) == summarize_timers(timers, NOW)


def test_ringing_timer_comes_first_with_zero_left():
    summary = summarize_timers(_by_id(_active("t1"), _ringing("t2")), NOW)
    assert summary.status == "ringing"
    assert (summary.remaining_time, summary.remaining_percentage) == (0, 0)
    assert [entry.id for entry in summary.timers] == ["t2", "t1"]


def test_active_timer_is_shown_before_a_paused_one():
    summary = summarize_timers(_by_id(_paused("t1", remaining=1.0), _active("t2", ends_in=50.0)), NOW)
    assert summary.status == "active"
    assert [entry.id for entry in summary.timers] == ["t2", "t1"]


def test_timers_are_ordered_nearest_first():
    summary = summarize_timers(_by_id(_active("t1", ends_in=50.0), _active("t2", ends_in=10.0), _paused("t3", remaining=40.0), _paused("t4", remaining=5.0)), NOW)
    assert [entry.id for entry in summary.timers] == ["t2", "t1", "t4", "t3"]


def test_ended_timers_are_not_listed():
    finished = Timer("t1", "pasta", 60, TimerStatus.FINISHED, NOW, ended_at=NOW)
    cancelled = Timer("t2", "pasta", 60, TimerStatus.CANCELLED, NOW, ended_at=NOW)
    assert summarize_timers(_by_id(finished, cancelled), NOW) == TimerSummary("idle", 0, 0)


def test_overdue_active_timer_has_zero_left():
    assert summarize_timers(_by_id(_active(ends_in=-5.0)), NOW).remaining_time == 0


def test_timer_without_total_has_zero_percentage():
    assert summarize_timers(_by_id(_active(total=0)), NOW).remaining_percentage == 0


def test_only_an_active_timer_is_running():
    assert has_running_timer(_by_id(_active()))
    assert not has_running_timer(_by_id(_paused(), _ringing("t2")))
