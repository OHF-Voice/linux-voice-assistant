"""Unit tests for network_refresh."""

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest

from linux_voice_assistant.network_refresh import changed_address, next_retry_delay, read_interface_ipv4, refresh_address_after_disconnect

BOUND = "192.168.1.10"
OTHER = "192.168.1.20"


class TestChangedAddress:
    def test_same_address_is_not_a_change(self):
        assert changed_address(bound_ip=BOUND, observed_ip=BOUND) is None

    def test_new_address_is_returned(self):
        assert changed_address(bound_ip=BOUND, observed_ip=OTHER) == OTHER

    def test_no_address_is_not_a_change(self):
        assert changed_address(bound_ip=BOUND, observed_ip=None) is None


class TestNextRetryDelay:
    def test_sequence_doubles_up_to_cap(self):
        delays = []
        current = 0.0
        for _ in range(9):
            current = next_retry_delay(current)
            delays.append(current)
        assert delays == [1, 2, 4, 8, 16, 32, 60, 60, 60]


class TestReadInterfaceIpv4:
    def test_returns_address(self):
        with patch("linux_voice_assistant.network_refresh.get_default_ipv4", return_value=BOUND):
            assert read_interface_ipv4("wlan0") == BOUND

    def test_no_address_is_none(self):
        with patch("linux_voice_assistant.network_refresh.get_default_ipv4", return_value=None):
            assert read_interface_ipv4("wlan0") is None

    @pytest.mark.parametrize("error", [ValueError("You must specify a valid interface name."), OSError("gone"), KeyError("addr")])
    def test_errors_are_none(self, error):
        with patch("linux_voice_assistant.network_refresh.get_default_ipv4", side_effect=error):
            assert read_interface_ipv4("wlan0") is None


async def run_refresh(observations, apply, *, sleep_calls=None, disconnects=1):
    """Signal disconnects, let the loop consume all observations, then cancel it."""
    client_disconnected = asyncio.Event()
    remaining = list(observations)
    finished = asyncio.Event()
    real_sleep = asyncio.sleep

    def observe(_interface):
        value = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        if len(remaining) == 1:
            finished.set()
        return value

    async def no_wait(delay):
        if sleep_calls is not None:
            sleep_calls.append(delay)
        await real_sleep(0)

    with patch("linux_voice_assistant.network_refresh.asyncio.sleep", no_wait):
        task = asyncio.create_task(refresh_address_after_disconnect(interface="wlan0", bound_ip=BOUND, client_disconnected=client_disconnected, apply=apply, observe=observe))
        for _ in range(disconnects):
            client_disconnected.set()
            await real_sleep(0)
            await real_sleep(0)
        await asyncio.wait_for(finished.wait(), 1)
        for _ in range(10):
            await real_sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class TestRefreshAddressAfterDisconnect:
    async def test_applies_new_address(self):
        apply = AsyncMock()
        await run_refresh([OTHER, OTHER], apply)
        apply.assert_awaited_once_with(OTHER)

    async def test_unchanged_address_does_nothing(self, caplog):
        apply = AsyncMock()
        with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.network_refresh"):
            await run_refresh([BOUND, BOUND], apply)
        apply.assert_not_awaited()
        assert "interface wlan0 still has 192.168.1.10" in caplog.text

    async def test_nothing_happens_without_a_disconnect(self):
        apply = AsyncMock()
        task = asyncio.create_task(refresh_address_after_disconnect(interface="wlan0", bound_ip=BOUND, client_disconnected=asyncio.Event(), apply=apply, observe=lambda _i: OTHER))
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        apply.assert_not_awaited()

    async def test_waits_with_growing_delay_until_interface_has_an_address(self, caplog):
        apply, sleeps = AsyncMock(), []
        with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.network_refresh"):
            await run_refresh([None, None, None, OTHER, OTHER], apply, sleep_calls=sleeps)
        assert sleeps[:3] == [1, 2, 4]
        apply.assert_awaited_once_with(OTHER)
        assert "has no IPv4 address (listener is bound to 192.168.1.10)" in caplog.text
        assert "Next network address check in 2 s" in caplog.text

    async def test_failed_apply_is_retried(self, caplog):
        apply = AsyncMock(side_effect=[OSError("cannot assign"), None])
        with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.network_refresh"):
            await run_refresh([OTHER, OTHER, OTHER], apply)
        assert apply.await_args_list[0] == apply.await_args_list[1]
        assert "Moving the ESPHome server to 192.168.1.20 failed: cannot assign" in caplog.text

    async def test_moved_address_is_remembered_for_the_next_disconnect(self):
        apply = AsyncMock()
        await run_refresh([OTHER, OTHER, OTHER], apply, disconnects=2)
        apply.assert_awaited_once_with(OTHER)
