"""Unit tests for network."""

import asyncio
import logging
import socket
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from linux_voice_assistant.network import (
    DEFAULT_KEEPALIVE,
    EsphomeEndpoint,
    KeepaliveSettings,
    apply_keepalive,
    changed_address,
    keepalive_socket_options,
    next_retry_delay,
    read_interface_ipv4,
    refresh_address_after_disconnect,
)

SETTINGS = KeepaliveSettings(idle_seconds=7, interval_seconds=3, probe_count=2, user_timeout_ms=15_000)


class TestKeepaliveSocketOptions:
    def test_enables_keepalive_first(self):
        assert keepalive_socket_options(SETTINGS)[0] == (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

    def test_maps_settings_to_tcp_options(self):
        options = keepalive_socket_options(SETTINGS)
        assert (socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 7) in options
        assert (socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 3) in options
        assert (socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 2) in options
        assert (socket.IPPROTO_TCP, socket.TCP_USER_TIMEOUT, 15_000) in options

    def test_skips_options_the_platform_lacks(self, monkeypatch):
        monkeypatch.delattr(socket, "TCP_USER_TIMEOUT")
        options = keepalive_socket_options(SETTINGS)
        assert all(option != getattr(socket, "TCP_USER_TIMEOUT", -1) for _level, option, _value in options)
        assert len(options) == 4

    def test_default_dead_peer_is_noticed_within_ten_seconds(self):
        worst_case = DEFAULT_KEEPALIVE.idle_seconds + DEFAULT_KEEPALIVE.interval_seconds * DEFAULT_KEEPALIVE.probe_count
        assert worst_case <= 10
        assert DEFAULT_KEEPALIVE.user_timeout_ms <= 10_000


class TestApplyKeepalive:
    def test_sets_options_on_real_socket(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            assert apply_keepalive(sock, SETTINGS) is True
            assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) == 1
            assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE) == 7
            assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL) == 3
            assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT) == 2
            assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_USER_TIMEOUT) == 15_000

    def test_without_socket_does_nothing(self):
        assert apply_keepalive(None) is False

    def test_failure_is_swallowed_and_logged(self, caplog):
        sock = MagicMock()
        sock.setsockopt.side_effect = OSError("not supported")
        with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.network"):
            assert apply_keepalive(sock, SETTINGS) is False
        assert "Could not enable TCP keepalive: not supported" in caplog.text

    def test_logs_settings_once_applied(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.network"):
            apply_keepalive(MagicMock(), SETTINGS)
        assert "TCP keepalive enabled (idle 7 s, interval 3 s, 2 probes, user timeout 15000 ms)" in caplog.text

    def test_applies_every_option_to_the_socket(self):
        sock = MagicMock()
        apply_keepalive(sock, SETTINGS)
        assert sock.setsockopt.call_count == len(keepalive_socket_options(SETTINGS))


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
        with patch("linux_voice_assistant.network.get_default_ipv4", return_value=BOUND):
            assert read_interface_ipv4("wlan0") == BOUND

    def test_no_address_is_none(self):
        with patch("linux_voice_assistant.network.get_default_ipv4", return_value=None):
            assert read_interface_ipv4("wlan0") is None

    @pytest.mark.parametrize("error", [ValueError("You must specify a valid interface name."), OSError("gone"), KeyError("addr")])
    def test_errors_are_none(self, error):
        with patch("linux_voice_assistant.network.get_default_ipv4", side_effect=error):
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

    with patch("linux_voice_assistant.network.asyncio.sleep", no_wait):
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
        with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.network"):
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
        with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.network"):
            await run_refresh([None, None, None, OTHER, OTHER], apply, sleep_calls=sleeps)
        assert sleeps[:3] == [1, 2, 4]
        apply.assert_awaited_once_with(OTHER)
        assert "has no IPv4 address (listener is bound to 192.168.1.10)" in caplog.text
        assert "Next network address check in 2 s" in caplog.text

    async def test_failed_apply_is_retried(self, caplog):
        apply = AsyncMock(side_effect=[OSError("cannot assign"), None])
        with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.network"):
            await run_refresh([OTHER, OTHER, OTHER], apply)
        assert apply.await_args_list[0] == apply.await_args_list[1]
        assert "Moving the ESPHome server to 192.168.1.20 failed: cannot assign" in caplog.text

    async def test_moved_address_is_remembered_for_the_next_disconnect(self):
        apply = AsyncMock()
        await run_refresh([OTHER, OTHER, OTHER], apply, disconnects=2)
        apply.assert_awaited_once_with(OTHER)


OLD = "192.168.1.10"
NEW = "192.168.1.20"


def make_endpoint(*, discovery=True, listen_error=None):
    state = MagicMock(ip_address=OLD)
    server, new_server = MagicMock(), MagicMock()
    mdns = AsyncMock() if discovery else None
    listen = AsyncMock(return_value=new_server, side_effect=listen_error)
    endpoint = EsphomeEndpoint(state=state, bound_ip=OLD, server=server, discovery=mdns, listen=listen)
    return endpoint, state, server, new_server, mdns, listen


class TestApply:
    async def test_new_address_moves_listener_and_announcement(self):
        endpoint, state, server, _new_server, mdns, listen = make_endpoint()
        await endpoint.apply(NEW)
        listen.assert_awaited_once_with(NEW)
        server.close.assert_called_once()
        mdns.update_address.assert_awaited_once_with(NEW)
        assert state.ip_address == NEW

    async def test_new_listener_is_bound_before_the_old_one_is_closed(self):
        endpoint, _state, server, _new_server, _mdns, listen = make_endpoint()
        order = []

        async def record_listen(_ip):
            order.append("listen")
            return MagicMock()

        listen.side_effect = record_listen
        server.close.side_effect = lambda: order.append("close")
        await endpoint.apply(NEW)
        assert order == ["listen", "close"]

    async def test_failed_bind_changes_nothing(self):
        endpoint, state, server, _new_server, mdns, _listen = make_endpoint(listen_error=OSError("cannot assign"))
        with pytest.raises(OSError):
            await endpoint.apply(NEW)
        server.close.assert_not_called()
        mdns.update_address.assert_not_awaited()
        assert state.ip_address == OLD

    async def test_retry_after_failed_announcement_does_not_bind_again(self):
        endpoint, _state, _server, _new_server, mdns, listen = make_endpoint()
        mdns.update_address.side_effect = [OSError("no route"), None]
        with pytest.raises(OSError):
            await endpoint.apply(NEW)
        await endpoint.apply(NEW)
        listen.assert_awaited_once_with(NEW)
        assert mdns.update_address.await_count == 2

    async def test_same_address_keeps_the_listener(self):
        endpoint, state, server, _new_server, mdns, listen = make_endpoint()
        await endpoint.apply(OLD)
        listen.assert_not_awaited()
        server.close.assert_not_called()
        mdns.update_address.assert_awaited_once_with(OLD)
        assert state.ip_address == OLD

    async def test_without_mdns_only_the_listener_moves(self):
        endpoint, _state, server, _new_server, _mdns, listen = make_endpoint(discovery=False)
        await endpoint.apply(NEW)
        listen.assert_awaited_once_with(NEW)
        server.close.assert_called_once()


class TestClose:
    async def test_closes_the_current_server(self):
        endpoint, _state, server, _new_server, _mdns, _listen = make_endpoint()
        await endpoint.close()
        server.close.assert_called_once()

    async def test_closes_the_moved_server_after_a_rebind(self):
        endpoint, _state, server, new_server, _mdns, _listen = make_endpoint()
        await endpoint.apply(NEW)
        await endpoint.close()
        assert server.close.call_count == 1
        new_server.close.assert_called_once()
