"""Unit tests for the timers of version 2 of the peripheral API."""

from linux_voice_assistant.peripheral_api.v2_timers import TimerInfo, summarize_timers, update_timers

DATA = {"id": "t1", "name": "tea", "total_seconds": 300, "seconds_left": 300}


class TestUpdateTimers:
    def test_ticking_adds_a_timer_with_its_end_time(self):
        timers = update_timers({}, "timer_ticking", DATA, now=100.0)
        assert timers == {"t1": TimerInfo(total_seconds=300, ends_at=400.0)}

    def test_updated_replaces_the_end_time(self):
        timers = update_timers({"t1": TimerInfo(300, 400.0)}, "timer_updated", {**DATA, "seconds_left": 100}, now=150.0)
        assert timers["t1"].ends_at == 250.0

    def test_zero_seconds_left_removes_the_timer(self):
        timers = update_timers({"t1": TimerInfo(300, 400.0)}, "timer_updated", {**DATA, "seconds_left": 0}, now=150.0)
        assert timers == {}

    def test_ringing_removes_the_timer(self):
        timers = update_timers({"t1": TimerInfo(300, 400.0)}, "timer_ringing", {**DATA, "seconds_left": 0}, now=400.0)
        assert timers == {}

    def test_other_events_change_nothing(self):
        before = {"t1": TimerInfo(300, 400.0)}
        assert update_timers(before, "idle", None, now=1.0) == before

    def test_does_not_modify_its_input(self):
        before: dict = {}
        update_timers(before, "timer_ticking", DATA, now=1.0)
        assert before == {}

    def test_two_timers_are_kept_apart(self):
        timers = update_timers({}, "timer_ticking", DATA, now=0.0)
        timers = update_timers(timers, "timer_ticking", {**DATA, "id": "t2", "seconds_left": 60}, now=0.0)
        assert set(timers) == {"t1", "t2"}


class TestSummarizeTimers:
    def test_no_timer_is_idle(self):
        summary = summarize_timers({}, now=0.0, ringing=False)
        assert (summary.status, summary.remaining_time, summary.remaining_percentage) == ("idle", 0, 0)

    def test_counting_down_is_active_with_time_and_percentage(self):
        summary = summarize_timers({"t1": TimerInfo(300, 400.0)}, now=250.0, ringing=False)
        assert (summary.status, summary.remaining_time, summary.remaining_percentage) == ("active", 150, 50)

    def test_the_timer_that_ends_first_counts(self):
        timers = {"long": TimerInfo(600, 700.0), "short": TimerInfo(60, 130.0)}
        summary = summarize_timers(timers, now=100.0, ringing=False)
        assert (summary.remaining_time, summary.remaining_percentage) == (30, 50)

    def test_ringing_wins_over_active(self):
        assert summarize_timers({"t1": TimerInfo(300, 400.0)}, now=0.0, ringing=True).status == "ringing"

    def test_ringing_without_other_timers_has_no_time(self):
        summary = summarize_timers({}, now=0.0, ringing=True)
        assert (summary.status, summary.remaining_time) == ("ringing", 0)

    def test_time_left_is_rounded_up_and_never_negative(self):
        assert summarize_timers({"t1": TimerInfo(300, 100.4)}, now=100.0, ringing=False).remaining_time == 1
        assert summarize_timers({"t1": TimerInfo(300, 100.0)}, now=200.0, ringing=False).remaining_time == 0

    def test_percentage_stays_between_0_and_100(self):
        assert summarize_timers({"t1": TimerInfo(10, 500.0)}, now=0.0, ringing=False).remaining_percentage == 100

    def test_zero_total_has_no_percentage(self):
        assert summarize_timers({"t1": TimerInfo(0, 5.0)}, now=0.0, ringing=False).remaining_percentage == 0
