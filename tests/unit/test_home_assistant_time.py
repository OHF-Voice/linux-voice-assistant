"""Tests for the time of Home Assistant (linux_voice_assistant/home_assistant_time.py)."""

import asyncio
import logging
from unittest.mock import MagicMock, patch

import pytest
from aioesphomeapi.api_pb2 import AuthenticationRequest, GetTimeRequest, GetTimeResponse  # type: ignore[attr-defined]  # pylint: disable=no-name-in-module

from linux_voice_assistant.api_server import PROTO_TO_MESSAGE_TYPE
from linux_voice_assistant.home_assistant_time import MAX_CLOCK_OFFSET_SECONDS, check_clock_offset, clock_offset, request_time_periodically
from linux_voice_assistant.timer_alarm import TimerAlarm

from .conftest import make_satellite, make_state


def test_clock_offset_counts_the_cut_off_half_second():
    assert clock_offset(1000, 990.0) == pytest.approx(10.5)
    assert clock_offset(1000, 1000.5) == pytest.approx(0.0)


def test_small_offset_is_logged_at_debug(caplog):
    with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.home_assistant_time"):
        assert check_clock_offset(2.5)
    assert "Clock of Home Assistant is 2.5 s ahead of the own clock (limit 10.0 s)" in caplog.text
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


@pytest.mark.parametrize("offset", [MAX_CLOCK_OFFSET_SECONDS + 0.1, -MAX_CLOCK_OFFSET_SECONDS - 0.1])
def test_large_offset_is_an_error(caplog, offset):
    with caplog.at_level(logging.ERROR, logger="linux_voice_assistant.home_assistant_time"):
        assert not check_clock_offset(offset)
    assert "differs from the clock of Home Assistant" in caplog.text
    assert "(more than 10.0 s)" in caplog.text


async def test_time_is_requested_at_once_and_then_repeatedly():
    send = MagicMock()
    with patch("linux_voice_assistant.home_assistant_time.asyncio.sleep", side_effect=[None, asyncio.CancelledError]) as sleep:
        with pytest.raises(asyncio.CancelledError):
            await request_time_periodically(send, interval=30.0)

    assert send.call_count == 2
    assert isinstance(send.call_args.args[0][0], GetTimeRequest)
    sleep.assert_called_with(30.0)


def test_timer_alarm_uses_the_time_of_home_assistant(tmp_path):
    state = make_state(tmp_path)
    alarm = TimerAlarm(state)
    state.home_assistant_time_offset = 3600.0
    with patch("linux_voice_assistant.timer_alarm.time.time", return_value=1000.0):
        assert alarm.now() == 4600.0


def test_time_response_sets_the_offset(tmp_path):
    sat = make_satellite(tmp_path)
    with patch("linux_voice_assistant.satellite.time.time", return_value=990.0):
        list(sat.handle_message(GetTimeResponse(epoch_seconds=1000)))
    assert sat.state.home_assistant_time_offset == pytest.approx(10.5)


async def test_time_is_requested_after_connecting_until_disconnect(tmp_path):
    sat = make_satellite(tmp_path)
    sat._loop = asyncio.get_running_loop()  # pylint: disable=protected-access
    sat.process_packet(PROTO_TO_MESSAGE_TYPE[AuthenticationRequest], AuthenticationRequest().SerializeToString())
    task = sat._time_requests  # pylint: disable=protected-access
    assert task is not None
    await asyncio.sleep(0)

    sat.connection_lost(None)
    await asyncio.sleep(0)
    assert task.cancelled()
    assert sat._time_requests is None  # pylint: disable=protected-access
