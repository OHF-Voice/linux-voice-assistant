"""Explain what kind of client sent a first byte that is not an ESPHome plaintext preamble.

The ESPHome API port only speaks the plaintext protocol, which starts every frame with 0x00. A wrong first byte
is almost always a client that connected to the wrong port or with the wrong settings, so a hint tells the user what to check.
"""

from typing import Optional

_NOISE_PREAMBLE = 0x01
_TLS_HANDSHAKE_PREAMBLE = 0x16
_JSON_PREAMBLE = ord("{")
# First letters of GET, POST, PUT, HEAD, OPTIONS, DELETE, PATCH, CONNECT, TRACE
_HTTP_PREAMBLES = frozenset(ord(letter) for letter in "GPHODCT")


def describe_unexpected_preamble(preamble: int) -> Optional[str]:
    """
    Return a hint for a first byte that is not 0x00, or None if the byte is not recognized.
    :param preamble: First byte received on the ESPHome API port.
    :return: Short explanation of the likely cause and what to check.
    """
    if preamble == _NOISE_PREAMBLE:
        return "client expects an encrypted ESPHome connection, but encryption is not enabled in LVA (see --enable-encryption)"
    if preamble == _TLS_HANDSHAKE_PREAMBLE:
        return "client started a TLS handshake, but the ESPHome API port does not use TLS"
    if preamble == _JSON_PREAMBLE:
        return "client sent JSON, which is not the ESPHome API; the peripheral WebSocket API listens on --peripheral-port (default 6055)"
    if preamble in _HTTP_PREAMBLES:
        return "client sent an HTTP or WebSocket request; the peripheral WebSocket API listens on --peripheral-port (default 6055)"
    return None
