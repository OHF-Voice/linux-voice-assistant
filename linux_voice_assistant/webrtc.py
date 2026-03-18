import logging

_LOGGER = logging.getLogger(__name__)


class WebRTCProcessor:
    def __init__(self, agc_level: int = 0, ns_level: int = 0):
        from webrtc_noise_gain import AudioProcessor  # type: ignore[import-untyped]

        self.apm = AudioProcessor(agc_level, ns_level)
        self.agc_level = agc_level
        self.ns_level = ns_level
        self._buffer = bytearray()
        self.FRAME_SIZE_BYTES = 320  # 160 samples * 2 bytes (16-bit PCM)

    def update_settings(self, agc_level: int, ns_level: int):
        """Re-initialize processor if settings changed."""
        if self.agc_level != agc_level or self.ns_level != ns_level:
            from webrtc_noise_gain import AudioProcessor

            _LOGGER.debug("Updating WebRTC settings: Gain=%s, NS=%s", agc_level, ns_level)
            self.apm = AudioProcessor(agc_level, ns_level)
            self.agc_level = agc_level
            self.ns_level = ns_level

    def process(self, raw_bytes: bytes) -> bytes:
        """
        Buffer and process audio.
        Returns processed bytes (may be shorter than input if buffering).
        """
        self._buffer.extend(raw_bytes)
        processed_chunks: list[bytes] = []

        while len(self._buffer) >= self.FRAME_SIZE_BYTES:
            frame = bytes(self._buffer[: self.FRAME_SIZE_BYTES])
            del self._buffer[: self.FRAME_SIZE_BYTES]  # drain in-place

            result = self.apm.Process10ms(frame)
            processed_chunks.append(result.audio)

        return b"".join(processed_chunks)
