"""Unit tests for preamble_hint and its use in APIServer.data_received."""

import logging

import pytest

from linux_voice_assistant.preamble_hint import describe_unexpected_preamble
from tests.unit.test_api_server import make_server


def test_noise_preamble_points_to_encryption():
    assert "--enable-encryption" in describe_unexpected_preamble(0x01)


def test_tls_preamble_mentions_tls():
    assert "TLS" in describe_unexpected_preamble(0x16)


def test_json_preamble_points_to_peripheral_port():
    assert "6055" in describe_unexpected_preamble(ord("{"))


@pytest.mark.parametrize("letter", list("GPHODCT"))
def test_http_preamble_points_to_peripheral_port(letter):
    assert "6055" in describe_unexpected_preamble(ord(letter))


def test_unknown_preamble_has_no_hint():
    assert describe_unexpected_preamble(0x7F) is None


def test_data_received_logs_hint_for_http_request(caplog):
    server = make_server()
    with caplog.at_level(logging.ERROR):
        server.data_received(b"GET / HTTP/1.1\r\n")
    assert "Incorrect preamble: 71" in caplog.text
    assert "peripheral WebSocket API" in caplog.text


def test_data_received_logs_no_hint_for_unknown_preamble(caplog):
    server = make_server()
    with caplog.at_level(logging.ERROR):
        server.data_received(b"\x7f\x00\x00")
    assert "Incorrect preamble: 127" in caplog.text
    assert "Hint" not in caplog.text
