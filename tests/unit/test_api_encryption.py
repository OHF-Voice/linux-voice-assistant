"""Unit tests for APIServer's ESPHome API encryption (Noise)."""

import base64
import os

import pytest
from aioesphomeapi._frame_helper.noise_encryption import ESPHOME_NOISE_BACKEND
from aioesphomeapi.api_pb2 import HelloRequest, HelloResponse  # type: ignore[attr-defined]
from aioesphomeapi.core import MESSAGE_TYPE_TO_PROTO
from noise.connection import NoiseConnection  # type: ignore[import-untyped]

from linux_voice_assistant.api_server import APIServer, decode_encryption_key

PROTO_TO_MESSAGE_TYPE = {v: k for k, v in MESSAGE_TYPE_TO_PROTO.items()}
KEY = os.urandom(32)


class _Server(APIServer):
    def __init__(self, key):
        super().__init__("test-server", noise_psk=key, mac_address="AA:BB:CC:DD:EE:FF")
        self.written = []
        self.closed = False

        class _Transport:
            def writelines(_self, data):  # pylint: disable=no-self-argument
                self.written.append(b"".join(bytes(d) for d in data))

            def close(_self):  # pylint: disable=no-self-argument
                self.closed = True

        self._transport = _Transport()
        self._writelines = self._transport.writelines

    def handle_message(self, msg):
        return []


def _frames(data: bytes):
    """Split encrypted framing (0x01, 2-byte length, payload) into payloads."""
    out, pos = [], 0
    while pos < len(data):
        assert data[pos] == 0x01
        length = (data[pos + 1] << 8) | data[pos + 2]
        out.append(data[pos + 3 : pos + 3 + length])
        pos += 3 + length
    return out


def _client(key):
    client = NoiseConnection.from_name(b"Noise_NNpsk0_25519_ChaChaPoly_SHA256", backend=ESPHOME_NOISE_BACKEND)
    client.set_as_initiator()
    client.set_psks(key)
    client.set_prologue(b"NoiseAPIInit\x00\x00")
    client.start_handshake()
    return client


def _client_hello(client) -> bytes:
    """What aioesphomeapi sends first: an empty hello frame, then its handshake message."""
    handshake = b"\x00" + client.write_message()
    return bytes(b"\x01\x00\x00" + bytes((0x01, len(handshake) >> 8, len(handshake) & 0xFF)) + handshake)


def _handshake(server_key, client_key):
    server = _Server(server_key)
    client = _client(client_key)
    server.data_received(_client_hello(client))
    return server, client, [f for w in server.written for f in _frames(w)]


def test_server_hello_has_protocol_name_and_mac():
    _, _, frames = _handshake(KEY, KEY)
    assert frames[0] == b"\x01test-server\x00aabbccddeeff\x00"


def test_handshake_and_encrypted_round_trip():
    server, client, frames = _handshake(KEY, KEY)
    assert frames[1][0] == 0x00
    client.read_message(frames[1][1:])

    server.written.clear()
    msg_type = PROTO_TO_MESSAGE_TYPE[HelloRequest]
    data = HelloRequest(client_info="test").SerializeToString()
    frame = client.encrypt(bytes((msg_type >> 8, msg_type & 0xFF, len(data) >> 8, len(data) & 0xFF)) + data)
    server.data_received(bytes((0x01, len(frame) >> 8, len(frame) & 0xFF)) + frame)

    (frame,) = [f for w in server.written for f in _frames(w)]
    msg = client.decrypt(frame)
    assert (msg[0] << 8) | msg[1] == PROTO_TO_MESSAGE_TYPE[HelloResponse]
    assert HelloResponse.FromString(msg[4:]).name == "test-server"
    assert not server.closed


def test_wrong_key_is_refused_as_esphome_does():
    server, _, frames = _handshake(KEY, os.urandom(32))
    # What Home Assistant recognises as a wrong (or missing) encryption key.
    assert frames[-1] == b"\x01Handshake MAC failure"
    assert server.closed


def test_plaintext_client_is_told_encryption_is_required():
    server = _Server(KEY)
    server.data_received(b"\x00\x00\x01")  # plaintext HelloRequest framing
    assert server.written and server.written[0][0] == 0x01
    assert server.closed


def test_nothing_sent_before_handshake():
    server = _Server(KEY)
    server.send_messages([HelloResponse(name="x")])
    assert not server.written


def test_decode_encryption_key():
    assert decode_encryption_key(base64.b64encode(KEY).decode() + "\n") == KEY
    with pytest.raises(ValueError):
        decode_encryption_key(base64.b64encode(os.urandom(16)).decode())
    with pytest.raises(ValueError):
        decode_encryption_key("not base64!")
