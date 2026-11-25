"""Unit tests for the reconnect of Home Assistant after a late entity (late_entity_reconnect.py)."""

import asyncio
import logging
from unittest.mock import MagicMock

import pytest

from linux_voice_assistant.peripheral_api.late_entity_reconnect import LateEntityReconnect
from tests.unit.conftest import make_state


def make_connected_state(tmp_path):
    state = make_state(tmp_path)
    state.connected = True
    state.satellite = MagicMock()
    return state


@pytest.fixture(name="fast")
def fast_reconnect(monkeypatch):
    monkeypatch.setattr(LateEntityReconnect, "DEBOUNCE_S", 0.01)
    return LateEntityReconnect()


class TestSchedule:
    @pytest.mark.asyncio
    async def test_closes_the_connection_to_home_assistant_once(self, tmp_path, fast, caplog):
        state = make_connected_state(tmp_path)

        with caplog.at_level(logging.INFO):
            fast.schedule(state, asyncio.get_running_loop(), "light", "leds")
            fast.schedule(state, asyncio.get_running_loop(), "button", "button_press")
            await asyncio.sleep(0.1)

        state.satellite._transport.close.assert_called_once()  # pylint: disable=protected-access
        assert "Late light 'leds' registered after HA handshake; forcing one HA reconnect" in caplog.text

    @pytest.mark.asyncio
    async def test_a_second_registration_while_one_is_planned_is_coalesced(self, tmp_path, fast, caplog):
        state = make_connected_state(tmp_path)
        with caplog.at_level(logging.DEBUG):
            fast.schedule(state, asyncio.get_running_loop(), "light", "leds")
            fast.schedule(state, asyncio.get_running_loop(), "button", "button_press")
            await asyncio.sleep(0.1)
        assert "Coalescing late button registration for 'button_press'" in caplog.text

    @pytest.mark.asyncio
    async def test_cooldown_prevents_a_second_reconnect(self, tmp_path, fast, caplog):
        state = make_connected_state(tmp_path)
        fast.schedule(state, asyncio.get_running_loop(), "light", "leds")
        await asyncio.sleep(0.1)

        with caplog.at_level(logging.INFO):
            fast.schedule(state, asyncio.get_running_loop(), "button", "button_press")
            await asyncio.sleep(0.1)

        state.satellite._transport.close.assert_called_once()  # pylint: disable=protected-access
        assert "Skipping HA reconnect after late button 'button_press': cooldown active" in caplog.text

    @pytest.mark.asyncio
    async def test_nothing_is_planned_while_home_assistant_is_not_connected(self, tmp_path, fast):
        state = make_connected_state(tmp_path)
        state.connected = False
        fast.schedule(state, asyncio.get_running_loop(), "light", "leds")
        await asyncio.sleep(0.05)
        state.satellite._transport.close.assert_not_called()  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_nothing_is_planned_without_a_satellite_or_loop(self, tmp_path, fast):
        state = make_connected_state(tmp_path)
        fast.schedule(state, None, "light", "leds")
        state.satellite = None
        fast.schedule(state, asyncio.get_running_loop(), "light", "leds")
        await asyncio.sleep(0.05)

    @pytest.mark.asyncio
    async def test_connection_that_ended_during_the_wait_is_left_alone(self, tmp_path, fast):
        state = make_connected_state(tmp_path)
        fast.schedule(state, asyncio.get_running_loop(), "light", "leds")
        state.connected = False
        await asyncio.sleep(0.1)
        state.satellite._transport.close.assert_not_called()  # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_no_transport_is_fine(self, tmp_path, fast):
        state = make_connected_state(tmp_path)
        state.satellite = MagicMock(spec=[])
        fast.schedule(state, asyncio.get_running_loop(), "light", "leds")
        await asyncio.sleep(0.1)


class TestCancel:
    @pytest.mark.asyncio
    async def test_a_planned_reconnect_does_not_happen(self, tmp_path):
        state = make_connected_state(tmp_path)
        reconnect = LateEntityReconnect()
        reconnect.schedule(state, asyncio.get_running_loop(), "light", "leds")

        reconnect.cancel()
        await asyncio.sleep(0.05)

        state.satellite._transport.close.assert_not_called()  # pylint: disable=protected-access

    def test_cancel_without_a_plan_does_nothing(self):
        LateEntityReconnect().cancel()
