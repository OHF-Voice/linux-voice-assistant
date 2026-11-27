"""Tests for reading the timer list of Home Assistant (linux_voice_assistant/home_assistant_timers.py)."""

import json
import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from aioesphomeapi.api_pb2 import (  # type: ignore[attr-defined]  # pylint: disable=no-name-in-module
    HomeassistantActionRequest,
    HomeassistantActionResponse,
    SubscribeHomeassistantServicesRequest,
)

from linux_voice_assistant.home_assistant_timers import (
    GET_TIMERS_ACTION,
    TimerSync,
    get_timers_request,
    parse_duration,
    parse_time,
    parse_timer_list,
    timer_from_item,
)
from linux_voice_assistant.peripheral_api import LVAEvent
from linux_voice_assistant.timer_alarm import TimerAlarm
from linux_voice_assistant.timers import Timer, TimerList, TimerStatus

from .conftest import make_satellite, make_state

NOW = 1_800_000_000.0
NOW_ISO = "2027-01-15T08:00:00+00:00"


def _item(**overrides):
    item = {"timer_id": "t1", "name": "pasta", "status": "active", "total_duration": 300, "finishes_at": NOW + 60}
    item.update(overrides)
    return item


def _response(*items):
    return json.dumps({"timer_list.kitchen_satellite_timers": {"timers": list(items)}}).encode()


# ---------------------------------------------------------------- request


def test_request_asks_for_the_timer_list_with_a_response():
    request = get_timers_request(7)
    assert isinstance(request, HomeassistantActionRequest)
    assert (request.service, request.wants_response, request.call_id) == (GET_TIMERS_ACTION, True, 7)


# ---------------------------------------------------------------- times and durations


def test_times_are_read_as_epoch_seconds():
    reference = datetime(2027, 1, 15, 8, 0, tzinfo=timezone.utc).timestamp()
    assert parse_time(NOW) == NOW
    assert parse_time(int(NOW)) == NOW
    for text in ("2027-01-15T08:00:00+00:00", "2027-01-15T09:00:00+01:00", "2027-01-15T08:00:00Z", "2027-01-15T08:00:00"):
        assert parse_time(text) == reference, text


@pytest.mark.parametrize("value", [None, "", "soon", True, [1]])
def test_unreadable_times_are_none(value):
    assert parse_time(value) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [(90, 90.0), (12.5, 12.5), ("0:05:00", 300.0), ("01:00:30.5", 3630.5), ("1 day, 2:00:00", 93600.0), ("2 days, 0:00:01", 172801.0), ("45", 45.0), ({"minutes": 5, "seconds": 3}, 303.0)],
)
def test_durations_are_read_as_seconds(value, expected):
    assert parse_duration(value) == pytest.approx(expected)


@pytest.mark.parametrize("value", [None, "five minutes", False, {"minutes": "x"}, [5]])
def test_unreadable_durations_are_none(value):
    assert parse_duration(value) is None


# ---------------------------------------------------------------- items


def test_active_item():
    assert timer_from_item(_item(), NOW) == Timer("t1", "pasta", 300, TimerStatus.ACTIVE, NOW, finishes_at=NOW + 60)


def test_paused_item_with_paused_remaining_or_remaining():
    expected = Timer("t1", "pasta", 300, TimerStatus.PAUSED, NOW, paused_remaining=42.0)
    assert timer_from_item(_item(status="paused", finishes_at=None, paused_remaining="0:00:42"), NOW) == expected
    assert timer_from_item(_item(status="paused", finishes_at=None, remaining=42), NOW) == expected


@pytest.mark.parametrize("status", ["finished", "cancelled"])
def test_ended_item_keeps_only_its_end(status):
    timer = timer_from_item(_item(status=status, ended_at=NOW_ISO), NOW)
    assert timer is not None
    assert (timer.status.value, timer.finishes_at, timer.ended_at) == (status, None, parse_time(NOW_ISO))


def test_created_duration_is_used_without_total_duration():
    assert timer_from_item(_item(total_duration=None, created_duration="0:01:00"), NOW).total_seconds == 60


@pytest.mark.parametrize(
    "item",
    [_item(status="ringing"), _item(timer_id=""), _item(status="active", finishes_at=None), _item(status="paused", finishes_at=None)],
)
def test_unreadable_items_are_none(item):
    assert timer_from_item(item, NOW) is None


# ---------------------------------------------------------------- answer


def test_timers_are_found_wherever_they_are_nested():
    assert [timer.timer_id for timer in parse_timer_list(_response(_item(), _item(timer_id="t2")), NOW)] == ["t1", "t2"]
    assert [timer.timer_id for timer in parse_timer_list(json.dumps([_item()]).encode(), NOW)] == ["t1"]


def test_empty_answer_is_an_empty_list():
    assert parse_timer_list(b"", NOW) == []
    assert parse_timer_list(_response(), NOW) == []


def test_answer_that_is_not_json_is_none(caplog):
    with caplog.at_level(logging.WARNING, logger="linux_voice_assistant.home_assistant_timers"):
        assert parse_timer_list(b"<html>", NOW) is None
    assert "Ignoring the timer list of Home Assistant, it is not JSON" in caplog.text


def test_unreadable_item_is_skipped(caplog):
    with caplog.at_level(logging.WARNING, logger="linux_voice_assistant.home_assistant_timers"):
        timers = parse_timer_list(_response(_item(), _item(timer_id="t2", status="ringing")), NOW)
    assert [timer.timer_id for timer in timers] == ["t1"]
    assert "Ignoring a timer of Home Assistant that cannot be read" in caplog.text


# ---------------------------------------------------------------- TimerSync


def test_answer_to_the_open_request_returns_the_timers_and_their_ids():
    sync = TimerSync()
    call_id = sync.start_request()
    timers = sync.handle_response(call_id, True, "", _response(_item()), NOW)
    assert [timer.timer_id for timer in timers] == ["t1"]
    assert sync.known_timer_ids == {"t1"}


def test_answer_to_an_old_request_is_ignored():
    sync = TimerSync()
    old = sync.start_request()
    new = sync.start_request()
    assert new != old
    assert sync.handle_response(old, True, "", _response(_item()), NOW) is None
    assert sync.known_timer_ids is None


def test_failure_logs_a_hint_once(caplog):
    sync = TimerSync()
    with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.home_assistant_timers"):
        assert sync.handle_response(sync.start_request(), False, "Service not found", b"", NOW) is None
        assert sync.handle_response(sync.start_request(), False, "Service not found", b"", NOW) is None
    infos = [record for record in caplog.records if record.levelno == logging.INFO]
    assert len(infos) == 1
    assert "Home Assistant could not perform timer_list.get_timers: Service not found. LVA keeps its own timer list" in infos[0].getMessage()


def test_no_answer_in_time_logs_a_hint(caplog):
    sync = TimerSync()
    call_id = sync.start_request()
    with caplog.at_level(logging.INFO, logger="linux_voice_assistant.home_assistant_timers"):
        sync.give_up(call_id)
    assert "Home Assistant did not answer timer_list.get_timers within 10 s" in caplog.text
    assert sync.handle_response(call_id, True, "", _response(_item()), NOW) is None


def test_give_up_after_the_answer_does_nothing(caplog):
    sync = TimerSync()
    call_id = sync.start_request()
    sync.handle_response(call_id, True, "", _response(), NOW)
    with caplog.at_level(logging.INFO, logger="linux_voice_assistant.home_assistant_timers"):
        sync.give_up(call_id)
    assert not caplog.records


# ---------------------------------------------------------------- merge through the timer alarm


def _alarm(tmp_path, timers=None):
    state = make_state(tmp_path, timers=TimerList(timers or {}))
    state.peripheral_api = MagicMock()
    state.timer_alarm = TimerAlarm(state, clock=lambda: NOW)
    return state, state.timer_alarm


def test_merge_takes_over_a_new_timer_and_tells_the_peripherals(tmp_path):
    state, alarm = _alarm(tmp_path)
    alarm.merge([Timer("t1", "pasta", 300, TimerStatus.ACTIVE, NOW, finishes_at=NOW + 60)])

    assert "t1" in state.timers.timers
    assert json.loads(state.preferences_path.read_text(encoding="utf-8"))["timers"]
    state.peripheral_api.emit_event_sync.assert_called_once_with(LVAEvent.TIMER_TICKING, {"id": "t1", "name": "pasta", "total_seconds": 300, "seconds_left": 60})
    state.peripheral_api.refresh_status_sync.assert_called_once()


def test_merge_rings_a_timer_that_finished_in_home_assistant(tmp_path):
    state, alarm = _alarm(tmp_path, {"t1": Timer("t1", "pasta", 300, TimerStatus.ACTIVE, NOW, finishes_at=NOW + 60)})
    alarm.merge([Timer("t1", "pasta", 300, TimerStatus.FINISHED, NOW, ended_at=NOW - 5)])

    assert alarm.ringing
    assert state.peripheral_api.emit_event_sync.call_args_list[0].args[0] == LVAEvent.TIMER_RINGING


def test_merge_stops_a_timer_cancelled_in_home_assistant(tmp_path):
    state, alarm = _alarm(tmp_path)
    alarm.merge([Timer("t1", "pasta", 300, TimerStatus.ACTIVE, NOW, finishes_at=NOW)])
    assert alarm.ringing

    alarm.merge([Timer("t1", "pasta", 300, TimerStatus.CANCELLED, NOW, ended_at=NOW)])
    assert not alarm.ringing
    assert state.peripheral_api.emit_event_sync.call_args_list[-1].args == (LVAEvent.IDLE, None)


def test_merge_without_change_tells_nobody(tmp_path):
    timer = Timer("t1", "pasta", 300, TimerStatus.ACTIVE, NOW, finishes_at=NOW + 60)
    state, alarm = _alarm(tmp_path, {"t1": timer})
    alarm.merge([timer])

    state.peripheral_api.emit_event_sync.assert_not_called()
    state.peripheral_api.refresh_status_sync.assert_not_called()


def test_cleanup_keeps_paused_timers_that_home_assistant_knows(tmp_path):
    old = NOW - 8 * 24 * 3600
    paused = {"t1": Timer("t1", "pasta", 300, TimerStatus.PAUSED, old, paused_remaining=30.0), "t2": Timer("t2", "rice", 300, TimerStatus.PAUSED, old, paused_remaining=30.0)}
    state, alarm = _alarm(tmp_path, paused)
    state.timer_sync.known_timer_ids = {"t1"}
    alarm.tick()

    assert set(state.timers.timers) == {"t1"}


# ---------------------------------------------------------------- satellite


def test_satellite_asks_for_the_timer_list_once_actions_are_subscribed(tmp_path):
    sat = make_satellite(tmp_path)
    sat.send_messages = MagicMock()
    list(sat.handle_message(SubscribeHomeassistantServicesRequest()))

    request = sat.send_messages.call_args.args[0][0]
    assert isinstance(request, HomeassistantActionRequest)
    assert request.service == GET_TIMERS_ACTION


def test_satellite_merges_the_answer(tmp_path):
    sat = make_satellite(tmp_path)
    sat.send_messages = MagicMock()
    sat.state.timer_alarm = TimerAlarm(sat.state, clock=lambda: NOW)
    list(sat.handle_message(SubscribeHomeassistantServicesRequest()))
    call_id = sat.send_messages.call_args.args[0][0].call_id

    list(sat.handle_message(HomeassistantActionResponse(call_id=call_id, success=True, response_data=_response(_item()))))

    assert sat.state.timers.timers["t1"].finishes_at == NOW + 60
