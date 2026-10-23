"""Unit tests for address-flap rebind decisions and the API idle timeout."""

import asyncio
import sys
import time
from unittest.mock import MagicMock

import pytest

from linux_voice_assistant.network_supervisor import (
    address_needs_rebind,
    connection_idle_expired,
    next_backoff_seconds,
    select_bind_address,
    watch_until_rebind,
)


class TestAddressNeedsRebind:
    def test_missing_ipv4_requires_rebind(self):
        assert address_needs_rebind(bound_ip="192.168.203.6", observed_ip=None, saw_gap=False) is True

    def test_different_ipv4_requires_rebind(self):
        assert address_needs_rebind(bound_ip="192.168.203.6", observed_ip="192.168.203.19", saw_gap=False) is True

    def test_same_ipv4_after_a_gap_requires_rebind(self):
        assert address_needs_rebind(bound_ip="192.168.203.6", observed_ip="192.168.203.6", saw_gap=True) is True

    def test_stable_address_does_not_rebind(self):
        assert address_needs_rebind(bound_ip="192.168.203.6", observed_ip="192.168.203.6", saw_gap=False) is False


class TestSelectBindAddress:
    def test_uses_observed_address_when_host_is_not_fixed(self):
        assert select_bind_address(fixed_host=None, observed_ip="10.0.0.8") == "10.0.0.8"

    def test_waits_when_fixed_host_is_not_configured(self):
        assert select_bind_address(fixed_host="192.168.203.6", observed_ip="10.0.0.8") is None

    def test_returns_fixed_host_when_it_is_present(self):
        assert select_bind_address(fixed_host="192.168.203.6", observed_ip="192.168.203.6") == "192.168.203.6"

    def test_missing_address_waits(self):
        assert select_bind_address(fixed_host=None, observed_ip=None) is None


class TestBackoff:
    def test_delay_doubles_until_cap(self):
        delay = next_backoff_seconds(0)
        assert delay == 1
        delay = next_backoff_seconds(delay)
        assert delay == 2
        delay = next_backoff_seconds(delay)
        assert delay == 4
        assert next_backoff_seconds(60) == 60
        assert next_backoff_seconds(40) == 60


class TestWatchUntilRebind:
    @pytest.mark.asyncio
    async def test_returns_when_address_disappears(self):
        observations = iter(["192.168.203.6", None])

        def observe(_interface: str):
            return next(observations)

        await asyncio.wait_for(watch_until_rebind("wlan0", "192.168.203.6", observe, interval=0), timeout=1)


class TestIdleTimeout:
    def test_recent_activity_is_not_expired(self):
        now = 100.0
        assert connection_idle_expired(authenticated=True, last_rx=now, now=now, limit=90) is False

    def test_silence_past_the_limit_is_expired(self):
        assert connection_idle_expired(authenticated=True, last_rx=0, now=90, limit=90) is True

    def test_unauthenticated_silence_is_not_expired(self):
        assert connection_idle_expired(authenticated=False, last_rx=0, now=120, limit=90) is False

    @pytest.mark.asyncio
    async def test_watch_closes_transport_when_silent(self, tmp_path):
        sys.modules.setdefault("mpv", MagicMock())
        from tests.unit.conftest import make_satellite

        satellite = make_satellite(tmp_path)
        satellite._api_authenticated = True
        satellite._last_rx = time.monotonic() - 10
        satellite._api_idle_timeout = 1
        satellite._api_idle_check_interval = 0.01
        satellite._transport = MagicMock()

        await satellite._watch_api_idle()

        satellite._transport.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_watch_keeps_connection_after_recent_frame(self, tmp_path):
        sys.modules.setdefault("mpv", MagicMock())
        from tests.unit.conftest import make_satellite

        satellite = make_satellite(tmp_path)
        satellite._api_authenticated = True
        satellite._last_rx = time.monotonic()
        satellite._api_idle_timeout = 90
        satellite._api_idle_check_interval = 0.01
        satellite._transport = MagicMock()

        task = asyncio.create_task(satellite._watch_api_idle())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        satellite._transport.close.assert_not_called()
