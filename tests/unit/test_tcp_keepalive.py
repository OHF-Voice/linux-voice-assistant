"""Unit tests for tcp_keepalive."""

import logging
import socket
from unittest.mock import MagicMock

from linux_voice_assistant.tcp_keepalive import DEFAULT_KEEPALIVE, KeepaliveSettings, apply_keepalive, keepalive_socket_options

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
        with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.tcp_keepalive"):
            assert apply_keepalive(sock, SETTINGS) is False
        assert "Could not enable TCP keepalive: not supported" in caplog.text

    def test_logs_settings_once_applied(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.tcp_keepalive"):
            apply_keepalive(MagicMock(), SETTINGS)
        assert "TCP keepalive enabled (idle 7 s, interval 3 s, 2 probes, user timeout 15000 ms)" in caplog.text

    def test_applies_every_option_to_the_socket(self):
        sock = MagicMock()
        apply_keepalive(sock, SETTINGS)
        assert sock.setsockopt.call_count == len(keepalive_socket_options(SETTINGS))
