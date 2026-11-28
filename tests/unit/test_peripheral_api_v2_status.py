"""Unit tests for the status of version 2 of the peripheral API."""

import pytest

from linux_voice_assistant.peripheral_api.v2.status import StatusInputs, V2Status, derive_status, pipeline_state_after, status_messages
from linux_voice_assistant.peripheral_api.v2.timers import TimerEntry, TimerSummary

IDLE_TIMER = TimerSummary("idle", 0, 0)


def make_inputs(**overrides) -> StatusInputs:
    values = dict(connected=True, muted=False, volume=0.5, announcing=False, pipeline_state="idle", media_state="idle", timer=IDLE_TIMER)
    values.update(overrides)
    return StatusInputs(**values)  # type: ignore[arg-type]


class TestPipelineStateAfter:
    @pytest.mark.parametrize(
        "event,expected",
        [
            ("wake_word_detected", "wake_word"),
            ("listening", "listening"),
            ("speech_detected", "speech_detected"),
            ("thinking", "thinking"),
            ("tts_speaking", "tts"),
            ("tts_finished", "idle"),
            ("idle", "idle"),
            ("pipeline_error", "pipeline_error"),
            ("wake_word_rejected", "wake_word_rejected"),
        ],
    )
    def test_events_move_the_pipeline(self, event, expected):
        assert pipeline_state_after(event, "listening") == expected

    @pytest.mark.parametrize("current", ["idle", "wake_word", "speech_detected", "thinking", "tts"])
    def test_speech_is_only_detected_while_listening(self, current):
        assert pipeline_state_after("speech_detected", current) == current

    @pytest.mark.parametrize("event", ["muted", "volume_changed", "timer_ticking", "zeroconf", "stt_text"])
    def test_other_events_keep_the_state(self, event):
        assert pipeline_state_after(event, "thinking") == "thinking"


class TestDeriveStatus:
    def test_basic_values(self):
        status = derive_status(make_inputs(), None)
        assert status == V2Status("connected", "idle", False, 50, "idle", 0, 0, "idle")

    def test_not_connected(self):
        assert derive_status(make_inputs(connected=False), None).lva_status == "disconnected"

    def test_volume_is_a_percentage(self):
        assert derive_status(make_inputs(volume=0.37), None).volume == 37

    @pytest.mark.parametrize("pipeline", ["wake_word", "listening", "speech_detected", "thinking", "tts", "pipeline_error"])
    def test_state_follows_the_pipeline(self, pipeline):
        assert derive_status(make_inputs(pipeline_state=pipeline), None).state == pipeline

    @pytest.mark.parametrize("pipeline", ["idle", "tts"])
    def test_announcing_shows_announce(self, pipeline):
        assert derive_status(make_inputs(announcing=True, pipeline_state=pipeline), None).state == "announce"

    def test_announcing_does_not_hide_listening(self):
        assert derive_status(make_inputs(announcing=True, pipeline_state="listening"), None).state == "listening"

    def test_playing_at_volume_zero_is_silent(self):
        assert derive_status(make_inputs(media_state="playing", volume=0.0), None).media_player_status == "silent"

    @pytest.mark.parametrize("media", ["idle", "playing", "pause"])
    def test_media_state_is_passed_on(self, media):
        assert derive_status(make_inputs(media_state=media), None).media_player_status == media

    def test_media_status_is_held_while_announcing(self):
        previous = derive_status(make_inputs(media_state="playing"), None)
        status = derive_status(make_inputs(announcing=True, media_state="pause"), previous)
        assert status.media_player_status == "playing"

    def test_media_status_is_taken_while_announcing_without_a_previous_status(self):
        assert derive_status(make_inputs(announcing=True, media_state="pause"), None).media_player_status == "pause"

    def test_timer_values_are_passed_on(self):
        status = derive_status(make_inputs(timer=TimerSummary("active", 90, 30)), None)
        assert (status.timer_status, status.timer_remaining_time, status.timer_remaining_percentage) == ("active", 90, 30)


class TestStatusMessages:
    def test_everything_for_a_new_peripheral_in_a_fixed_order(self):
        messages = status_messages(None, derive_status(make_inputs(), None))
        assert [m["event"] for m in messages] == [
            "lva_status",
            "timer_status",
            "timer_remaining_time",
            "timer_remaining_percentage",
            "timer_full_data_set",
            "state",
            "mute_status",
            "volume_status",
            "media_player_status",
        ]

    def test_message_shapes(self):
        messages = {m["event"]: m["data"] for m in status_messages(None, derive_status(make_inputs(muted=True, volume=0.8), None))}
        assert messages["lva_status"] == {"status": "connected"}
        assert messages["state"] == {"state": "idle"}
        assert messages["mute_status"] == {"status": "muted"}
        assert messages["volume_status"] == {"volume": 80}
        assert messages["timer_status"] == {"status": "idle", "auto_stopped": False}
        assert messages["timer_full_data_set"] == {"timers": []}
        assert messages["timer_remaining_time"] == {"seconds": 0}
        assert messages["timer_remaining_percentage"] == {"percentage": 0}
        assert messages["media_player_status"] == {"status": "idle"}

    def test_nothing_if_nothing_changed(self):
        status = derive_status(make_inputs(), None)
        assert not status_messages(status, status)

    def test_only_what_changed(self):
        before = derive_status(make_inputs(), None)
        after = derive_status(make_inputs(pipeline_state="listening", volume=0.6), before)
        assert [m["event"] for m in status_messages(before, after)] == ["state", "volume_status"]

    def test_an_ended_timer_comes_before_the_new_state(self):
        before = derive_status(make_inputs(timer=TimerSummary("ringing", 0, 0)), None)
        after = derive_status(make_inputs(pipeline_state="wake_word"), before)
        assert [m["event"] for m in status_messages(before, after)] == ["timer_status", "state"]

    def test_unmuted_is_reported_as_unmuted(self):
        before = derive_status(make_inputs(muted=True), None)
        after = derive_status(make_inputs(muted=False), before)
        assert status_messages(before, after) == [{"event": "mute_status", "data": {"status": "unmuted"}}]

    def test_a_timer_that_stopped_on_its_own_says_so_in_the_timer_status(self):
        before = derive_status(make_inputs(timer=TimerSummary("ringing", 0, 0)), None)
        after = derive_status(make_inputs(timer_auto_stopped=True), before)
        assert status_messages(before, after) == [{"event": "timer_status", "data": {"status": "idle", "auto_stopped": True}}]

    def test_the_flag_alone_sends_nothing_new(self):
        before = derive_status(make_inputs(timer_auto_stopped=True), None)
        after = derive_status(make_inputs(timer_auto_stopped=False), before)
        assert not status_messages(before, after)

    def test_all_timers_are_sent_with_their_data(self):
        timer = TimerEntry("t1", "tea", "active", 300, 150, 50)
        status = derive_status(make_inputs(timer=TimerSummary("active", 150, 50, (timer,))), None)
        data = {m["event"]: m["data"] for m in status_messages(None, status)}["timer_full_data_set"]
        assert data == {"timers": [{"id": "t1", "name": "tea", "status": "active", "total_seconds": 300, "seconds_left": 150, "percentage": 50}]}

    def test_the_full_data_set_is_sent_again_when_a_timer_changes(self):
        first = derive_status(make_inputs(timer=TimerSummary("active", 150, 50, (TimerEntry("t1", "tea", "active", 300, 150, 50),))), None)
        second = derive_status(make_inputs(timer=TimerSummary("active", 149, 50, (TimerEntry("t1", "tea", "active", 300, 149, 50),))), first)
        assert "timer_full_data_set" in [m["event"] for m in status_messages(first, second)]
