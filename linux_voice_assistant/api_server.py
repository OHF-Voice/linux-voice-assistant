"""Partial ESPHome server implementation."""

import asyncio
import binascii
import logging
import threading
from abc import abstractmethod
from collections.abc import Iterable
from typing import TYPE_CHECKING, Optional

# pylint: disable=no-name-in-module
from aioesphomeapi._frame_helper.noise_encryption import ESPHOME_NOISE_BACKEND
from aioesphomeapi._frame_helper.packets import make_plain_text_packets
from aioesphomeapi.api_pb2 import (  # type: ignore[attr-defined]
    AuthenticationRequest,
    AuthenticationResponse,
    DisconnectRequest,
    DisconnectResponse,
    HelloRequest,
    HelloResponse,
    PingRequest,
    PingResponse,
)
from aioesphomeapi.core import MESSAGE_TYPE_TO_PROTO
from cryptography.exceptions import InvalidTag
from google.protobuf import message
from noise.connection import NoiseConnection  # type: ignore[import-untyped]

PROTO_TO_MESSAGE_TYPE = {v: k for k, v in MESSAGE_TYPE_TO_PROTO.items()}

_LOGGER = logging.getLogger(__name__)

# ESPHome API encryption, as in ESPHome's own API server (api_frame_helper_noise.cpp).
NOISE_PROTOCOL = b"Noise_NNpsk0_25519_ChaChaPoly_SHA256"
NOISE_PROLOGUE = b"NoiseAPIInit\x00\x00"
# Chosen protocol in the server hello: the only one, NOISE_PROTOCOL.
NOISE_PROTOCOL_ID = 0x01

_NOISE_STATE_HELLO = 1
_NOISE_STATE_HANDSHAKE = 2
_NOISE_STATE_READY = 3
_NOISE_STATE_FAILED = 4


def decode_encryption_key(key: str) -> bytes:
    """Decode an ESPHome API encryption key (32 bytes, base64)."""
    try:
        key_bytes = binascii.a2b_base64(key.strip())
    except (binascii.Error, ValueError) as err:
        raise ValueError("Encryption key is not valid base64") from err
    if len(key_bytes) != 32:
        raise ValueError("Encryption key must be 32 bytes (base64), for example from: openssl rand -base64 32")
    return key_bytes


class APIServer(asyncio.Protocol):

    def __init__(self, name: str, noise_psk: Optional[bytes] = None, mac_address: str = "") -> None:
        """Serve the ESPHome API to one client.

        With noise_psk (32 bytes), only clients that have the same encryption key are served,
        as ESPHome devices with api: encryption: key: do; without it, plaintext only.
        mac_address is sent in the encrypted server hello (ESPHome's format: lowercase, no colons).
        """
        self.name = name
        self._noise_psk = noise_psk
        self._mac_address = mac_address.replace(":", "").lower()
        self._noise_state = _NOISE_STATE_HELLO
        self._noise: Optional[NoiseConnection] = None

        self._buffer: Optional[bytes] = None
        self._buffer_len: int = 0
        self._pos: int = 0
        self._transport = None
        self._writelines = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread_id: Optional[int] = None

    @abstractmethod
    def handle_message(self, msg: message.Message) -> Iterable[message.Message]:
        pass

    def process_packet(self, msg_type: int, packet_data: bytes) -> None:
        msg_class = MESSAGE_TYPE_TO_PROTO[msg_type]
        msg_inst = msg_class.FromString(packet_data)

        if isinstance(msg_inst, HelloRequest):
            self.send_messages(
                [
                    HelloResponse(
                        api_version_major=1,
                        api_version_minor=10,
                        name=self.name,
                    )
                ]
            )
            return

        if isinstance(msg_inst, AuthenticationRequest):
            self.send_messages([AuthenticationResponse()])
        elif isinstance(msg_inst, DisconnectRequest):
            self.send_messages([DisconnectResponse()])
            _LOGGER.debug("Disconnect requested")
            if self._transport:
                self._transport.close()
                self._transport = None
                self._writelines = None
        elif isinstance(msg_inst, PingRequest):
            self.send_messages([PingResponse()])
        elif msgs := self.handle_message(msg_inst):
            if isinstance(msgs, message.Message):
                msgs = [msgs]

            self.send_messages(msgs)

    def send_messages(self, msgs: Iterable[message.Message]):
        if self._writelines is None or not msgs:
            return

        packets = [(PROTO_TO_MESSAGE_TYPE[msg.__class__], msg.SerializeToString()) for msg in msgs]
        if self._loop is not None and self._loop_thread_id is not None and threading.get_ident() != self._loop_thread_id:
            # Encrypted frames carry a nonce each: they are encrypted where they are written, in order.
            self._loop.call_soon_threadsafe(self._write_packets, packets)
            return

        self._write_packets(packets)

    def _write_packets(self, packets: "list[tuple[int, bytes]]") -> None:
        if self._writelines is None:
            return

        if self._noise_psk is None:
            self._writelines(make_plain_text_packets(packets))
            return

        if self._noise_state != _NOISE_STATE_READY or self._noise is None:
            _LOGGER.debug("Dropping %s message(s) sent before the encryption handshake", len(packets))
            return

        out = []
        for msg_type, data in packets:
            # Encrypted: 2-byte type, 2-byte length, data (both big-endian).
            frame = self._noise.encrypt(bytes(((msg_type >> 8) & 0xFF, msg_type & 0xFF, (len(data) >> 8) & 0xFF, len(data) & 0xFF)) + data)
            out.append(bytes((0x01, (len(frame) >> 8) & 0xFF, len(frame) & 0xFF)))
            out.append(frame)
        self._writelines(out)

    def connection_made(self, transport) -> None:
        self._transport = transport
        self._writelines = transport.writelines
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        else:
            self._loop_thread_id = threading.get_ident()

    def data_received(self, data: bytes):
        if self._buffer is None:
            self._buffer = data
            self._buffer_len = len(data)
        else:
            self._buffer += data
            self._buffer_len += len(data)

        if self._noise_psk is not None:
            self._noise_data_received()
            return

        while self._buffer_len >= 3:
            self._pos = 0
            # Read preamble, which should always 0x00
            if (preamble := self._read_varuint()) != 0x00:
                _LOGGER.error("Incorrect preamble: %s", preamble)
                return

            if (length := self._read_varuint()) == -1:
                _LOGGER.error("Incorrect length: %s", length)
                return

            if (msg_type := self._read_varuint()) == -1:
                _LOGGER.error("Incorrect message type: %s", msg_type)
                return

            if length == 0:
                # Empty message (allowed)
                self._remove_from_buffer()
                self.process_packet(msg_type, b"")
                continue

            if (packet_data := self._read(length)) is None:
                return

            self._remove_from_buffer()
            self.process_packet(msg_type, packet_data)

    def _read(self, length: int) -> bytes | None:
        """Read exactly length bytes from the buffer or None if all the bytes are not yet available."""
        new_pos = self._pos + length
        if self._buffer_len < new_pos:
            return None
        original_pos = self._pos
        self._pos = new_pos
        if TYPE_CHECKING:
            assert self._buffer is not None, "Buffer should be set"
        cstr = self._buffer
        # Important: we must keep the bounds check (self._buffer_len < new_pos)
        # above to verify we never try to read past the end of the buffer
        return cstr[original_pos:new_pos]

    def connection_lost(self, exc):
        self._transport = None
        self._writelines = None
        self._loop = None
        self._loop_thread_id = None
        self._noise = None

    # ------------------------------------------------------------------
    # Encryption (ESPHome API Noise protocol)
    # ------------------------------------------------------------------

    def _noise_data_received(self) -> None:
        """Handle frames of an encrypted connection: 0x01, 2-byte big-endian length, payload."""
        while self._buffer_len >= 3 and self._noise_state != _NOISE_STATE_FAILED:
            if TYPE_CHECKING:
                assert self._buffer is not None, "Buffer should be set"
            self._pos = 0
            indicator = self._buffer[0]
            if indicator != 0x01:
                if indicator == 0x00:
                    _LOGGER.warning("Client connected without encryption; an encryption key is required")
                    # What ESPHome answers: the client sees an encrypted frame and reports that
                    # the device requires encryption.
                    self._noise_fail(b"\x01Bad indicator byte")
                else:
                    _LOGGER.error("Incorrect indicator byte: %s", indicator)
                    self._close()
                return

            frame_len = (self._buffer[1] << 8) | self._buffer[2]
            self._pos = 3
            if (frame := self._read(frame_len)) is None:
                return

            self._remove_from_buffer()
            if self._noise_state == _NOISE_STATE_READY:
                self._noise_handle_frame(frame)
            elif self._noise_state == _NOISE_STATE_HELLO:
                self._noise_handle_hello()
            elif self._noise_state == _NOISE_STATE_HANDSHAKE:
                self._noise_handle_handshake(frame)

    def _noise_handle_hello(self) -> None:
        """Answer the client hello (an empty frame) with the server hello."""
        self._write_noise_frame(bytes((NOISE_PROTOCOL_ID,)) + self.name.encode() + b"\x00" + self._mac_address.encode() + b"\x00")
        noise = NoiseConnection.from_name(NOISE_PROTOCOL, backend=ESPHOME_NOISE_BACKEND)
        noise.set_as_responder()
        if TYPE_CHECKING:
            assert self._noise_psk is not None
        noise.set_psks(self._noise_psk)
        noise.set_prologue(NOISE_PROLOGUE)
        noise.start_handshake()
        self._noise = noise
        self._noise_state = _NOISE_STATE_HANDSHAKE

    def _noise_handle_handshake(self, frame: bytes) -> None:
        """Complete the handshake: the client's message (after a 0x00 byte), then ours."""
        if TYPE_CHECKING:
            assert self._noise is not None
        if not frame or frame[0] != 0x00:
            _LOGGER.error("Malformed encryption handshake")
            self._noise_fail(b"\x01Bad handshake packet len" if not frame else b"\x01Bad handshake error byte")
            return
        try:
            self._noise.read_message(frame[1:])
        except InvalidTag:
            # A different key (or Home Assistant probing whether encryption is on).
            _LOGGER.warning("Client used a different encryption key")
            self._noise_fail(b"\x01Handshake MAC failure")
            return
        except Exception:  # pylint: disable=broad-exception-caught
            _LOGGER.exception("Encryption handshake failed")
            self._noise_fail(b"\x01Handshake error")
            return

        self._write_noise_frame(b"\x00" + self._noise.write_message())
        self._noise_state = _NOISE_STATE_READY
        _LOGGER.debug("Encrypted connection established")

    def _noise_handle_frame(self, frame: bytes) -> None:
        """Decrypt a message: 2-byte type, 2-byte length, data (both big-endian)."""
        if TYPE_CHECKING:
            assert self._noise is not None
        try:
            msg = self._noise.decrypt(frame)
        except InvalidTag:
            _LOGGER.error("Could not decrypt a message; closing the connection")
            self._noise_state = _NOISE_STATE_FAILED
            self._close()
            return
        if len(msg) < 4:
            _LOGGER.error("Decrypted message too short: %s bytes", len(msg))
            self._noise_state = _NOISE_STATE_FAILED
            self._close()
            return
        self.process_packet((msg[0] << 8) | msg[1], msg[4:])

    def _write_noise_frame(self, payload: bytes) -> None:
        if self._writelines is None:
            return
        self._writelines([bytes((0x01, (len(payload) >> 8) & 0xFF, len(payload) & 0xFF)), payload])

    def _noise_fail(self, payload: bytes) -> None:
        """Tell the client why (as ESPHome does), then close."""
        self._noise_state = _NOISE_STATE_FAILED
        self._write_noise_frame(payload)
        self._close()

    def _close(self) -> None:
        self._buffer = None
        self._buffer_len = 0
        if self._transport is not None:
            self._transport.close()

    def _read_varuint(self) -> int:
        """Read a varuint from the buffer or -1 if the buffer runs out of bytes."""
        if not self._buffer:
            return -1

        result = 0
        bitpos = 0
        cstr = self._buffer
        while self._buffer_len > self._pos:
            val = cstr[self._pos]
            self._pos += 1
            result |= (val & 0x7F) << bitpos
            if (val & 0x80) == 0:
                return result
            bitpos += 7
        return -1

    def _remove_from_buffer(self) -> None:
        """Remove data from the buffer."""
        end_of_frame_pos = self._pos
        self._buffer_len -= end_of_frame_pos
        if self._buffer_len == 0:
            # This is the best case scenario, we can just set the buffer to None
            # and don't have to copy the data. This is the most common case as well.
            self._buffer = None
            return
        if TYPE_CHECKING:
            assert self._buffer is not None, "Buffer should be set"
        # This is the worst case scenario, we have to copy the data
        # and can't just use the buffer directly. This should only happen
        # when we read multiple frames at once because the event loop
        # is blocked and we cannot pull the data out of the buffer fast enough.
        cstr = self._buffer
        # Important: we must use the explicit length for the slice
        # since Cython will stop at any '\0' character if we don't
        self._buffer = cstr[end_of_frame_pos : self._buffer_len + end_of_frame_pos]
