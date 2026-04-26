"""Unit tests for APIServer packet parsing and message handling."""

import pytest
from unittest.mock import MagicMock, patch, call
import asyncio

from aioesphomeapi.api_pb2 import (
    HelloRequest,
    HelloResponse,
    PingRequest,
    PingResponse,
    DisconnectRequest,
    DisconnectResponse,
    AuthenticationRequest,
    AuthenticationResponse,
)
from aioesphomeapi._frame_helper.packets import make_plain_text_packets
from aioesphomeapi.core import MESSAGE_TYPE_TO_PROTO

PROTO_TO_MESSAGE_TYPE = {v: k for k, v in MESSAGE_TYPE_TO_PROTO.items()}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_packet(msg) -> bytes:
    """Serialize a protobuf message into a plain-text ESPHome packet."""
    msg_type = PROTO_TO_MESSAGE_TYPE[msg.__class__]
    data = msg.SerializeToString()
    packets = make_plain_text_packets([(msg_type, data)])
    # make_plain_text_packets returns a list of memoryview/bytes — join into one
    if isinstance(packets, (list, tuple)):
        return b"".join(bytes(p) for p in packets)
    return bytes(packets)


class ConcreteAPIServer:
    """Concrete subclass of APIServer for testing — records handled messages."""

    def __init__(self):
        from linux_voice_assistant.api_server import APIServer

        class _Concrete(APIServer):
            def __init__(self_inner):
                super().__init__("test-server")
                self_inner.handled = []

            def handle_message(self_inner, msg):
                self_inner.handled.append(msg)
                return []

        self._cls = _Concrete
        self.instance = _Concrete()

    @property
    def server(self):
        return self.instance


def make_server():
    """Return a connected APIServer instance with a mock transport."""
    wrapper = ConcreteAPIServer()
    server = wrapper.server

    transport = MagicMock()
    written = []

    def capture_writelines(data):
        # data may be a list of memoryview/bytes — flatten to a single bytes object
        if isinstance(data, (list, tuple)):
            written.append(b"".join(bytes(d) for d in data))
        else:
            written.append(bytes(data))

    transport.writelines = capture_writelines
    server._transport = transport
    server._writelines = capture_writelines
    server._written = written

    return server


def get_sent_messages(server):
    """Decode all messages the server sent back via writelines."""
    from aioesphomeapi.core import MESSAGE_TYPE_TO_PROTO

    messages = []
    for raw in server._written:
        # raw is bytes — parse the plain-text framing manually
        pos = 0
        while pos < len(raw):
            # preamble
            if (raw[pos] if isinstance(raw[pos], int) else raw[pos][0]) != 0x00:
                break
            pos += 1

            # length varuint
            length = 0
            bitpos = 0
            while True:
                b = raw[pos]
                pos += 1
                length |= (b & 0x7F) << bitpos
                if not (b & 0x80):
                    break
                bitpos += 7

            # msg_type varuint
            msg_type = 0
            bitpos = 0
            while True:
                b = raw[pos]
                pos += 1
                msg_type |= (b & 0x7F) << bitpos
                if not (b & 0x80):
                    break
                bitpos += 7

            # payload
            payload = raw[pos:pos + length]
            pos += length

            msg_cls = MESSAGE_TYPE_TO_PROTO[msg_type]
            messages.append(msg_cls.FromString(payload))

    return messages


# ---------------------------------------------------------------------------
# connection_made / connection_lost
# ---------------------------------------------------------------------------


class TestConnection:
    def test_connection_made_stores_transport(self):
        from linux_voice_assistant.api_server import APIServer

        class _Concrete(APIServer):
            def __init__(self):
                super().__init__("test")
            def handle_message(self, msg):
                return []

        server = _Concrete()
        transport = MagicMock()
        transport.writelines = MagicMock()

        loop = MagicMock()
        with patch("linux_voice_assistant.api_server.asyncio.get_running_loop", return_value=loop):
            server.connection_made(transport)

        assert server._transport is transport

    def test_connection_lost_clears_transport(self):
        server = make_server()
        server.connection_lost(None)
        assert server._transport is None
        assert server._writelines is None

    def test_connection_lost_clears_loop(self):
        server = make_server()
        server._loop = MagicMock()
        server.connection_lost(None)
        assert server._loop is None


# ---------------------------------------------------------------------------
# HelloRequest → HelloResponse
# ---------------------------------------------------------------------------


class TestHelloHandshake:
    def test_hello_request_yields_hello_response(self):
        server = make_server()
        server.data_received(make_packet(HelloRequest(client_info="test")))
        msgs = get_sent_messages(server)
        assert any(isinstance(m, HelloResponse) for m in msgs)

    def test_hello_response_contains_server_name(self):
        server = make_server()
        server.data_received(make_packet(HelloRequest(client_info="test")))
        msgs = get_sent_messages(server)
        hello = next(m for m in msgs if isinstance(m, HelloResponse))
        assert hello.name == "test-server"

    def test_hello_response_has_api_version(self):
        server = make_server()
        server.data_received(make_packet(HelloRequest(client_info="test")))
        msgs = get_sent_messages(server)
        hello = next(m for m in msgs if isinstance(m, HelloResponse))
        assert hello.api_version_major == 1
        assert hello.api_version_minor == 10

    def test_hello_does_not_call_handle_message(self):
        server = make_server()
        server.data_received(make_packet(HelloRequest(client_info="test")))
        assert server.handled == []


# ---------------------------------------------------------------------------
# PingRequest → PingResponse
# ---------------------------------------------------------------------------


class TestPing:
    def test_ping_request_yields_ping_response(self):
        server = make_server()
        server.data_received(make_packet(PingRequest()))
        msgs = get_sent_messages(server)
        assert any(isinstance(m, PingResponse) for m in msgs)


# ---------------------------------------------------------------------------
# DisconnectRequest → DisconnectResponse
# ---------------------------------------------------------------------------


class TestDisconnect:
    def test_disconnect_request_yields_disconnect_response(self):
        server = make_server()
        server.data_received(make_packet(DisconnectRequest()))
        msgs = get_sent_messages(server)
        assert any(isinstance(m, DisconnectResponse) for m in msgs)

    def test_disconnect_closes_transport(self):
        server = make_server()
        mock_transport = MagicMock()
        server._transport = mock_transport
        server.data_received(make_packet(DisconnectRequest()))
        mock_transport.close.assert_called_once()

    def test_disconnect_clears_transport_reference(self):
        server = make_server()
        server.data_received(make_packet(DisconnectRequest()))
        assert server._transport is None


# ---------------------------------------------------------------------------
# AuthenticationRequest → AuthenticationResponse
# ---------------------------------------------------------------------------


class TestAuthentication:
    def test_auth_request_yields_auth_response(self):
        server = make_server()
        server.data_received(make_packet(AuthenticationRequest()))
        msgs = get_sent_messages(server)
        assert any(isinstance(m, AuthenticationResponse) for m in msgs)


# ---------------------------------------------------------------------------
# Buffer management
# ---------------------------------------------------------------------------


class TestBufferManagement:
    def test_buffer_is_none_after_complete_packet(self):
        server = make_server()
        server.data_received(make_packet(PingRequest()))
        assert server._buffer is None

    def test_partial_packet_stays_in_buffer(self):
        server = make_server()
        full = make_packet(PingRequest())
        # Send only half the packet
        server.data_received(full[:len(full) // 2])
        assert server._buffer is not None

    def test_split_packet_reassembled_correctly(self):
        server = make_server()
        full = make_packet(PingRequest())
        half = len(full) // 2
        server.data_received(full[:half])
        server.data_received(full[half:])
        msgs = get_sent_messages(server)
        assert any(isinstance(m, PingResponse) for m in msgs)

    def test_two_packets_in_one_data_received(self):
        server = make_server()
        data = make_packet(PingRequest()) + make_packet(PingRequest())
        server.data_received(data)
        msgs = get_sent_messages(server)
        assert sum(1 for m in msgs if isinstance(m, PingResponse)) == 2

    def test_buffer_len_tracks_correctly(self):
        server = make_server()
        full = make_packet(PingRequest())
        server.data_received(full[:2])
        assert server._buffer_len == 2

    def test_buffer_cleared_after_full_packet(self):
        server = make_server()
        server.data_received(make_packet(PingRequest()))
        assert server._buffer_len == 0


# ---------------------------------------------------------------------------
# _read_varuint
# ---------------------------------------------------------------------------


class TestReadVarint:
    def _make_server_with_buffer(self, data: bytes):
        server = make_server()
        server._buffer = data
        server._buffer_len = len(data)
        server._pos = 0
        return server

    def test_reads_single_byte_varuint(self):
        server = self._make_server_with_buffer(bytes([0x05]))
        assert server._read_varuint() == 5

    def test_reads_two_byte_varuint(self):
        # 300 encoded as varuint = 0xAC 0x02
        server = self._make_server_with_buffer(bytes([0xAC, 0x02]))
        assert server._read_varuint() == 300

    def test_returns_minus_one_on_empty_buffer(self):
        server = make_server()
        server._buffer = None
        assert server._read_varuint() == -1

    def test_returns_zero_for_zero_byte(self):
        server = self._make_server_with_buffer(bytes([0x00]))
        assert server._read_varuint() == 0