import logging

import numpy as np

_LOGGER = logging.getLogger(__name__)


class WebRTCProcessor:
    def __init__(self, agc_level: int = 0, ns_level: int = 0):
        from webrtc_noise_gain import AudioProcessor  # type: ignore[import-untyped]

        self.apm = AudioProcessor(agc_level, ns_level)
        self.agc_level = agc_level
        self.ns_level = ns_level
        self._buffer = b""
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
        self._buffer += raw_bytes
        processed_output = b""

        while len(self._buffer) >= self.FRAME_SIZE_BYTES:
            frame = self._buffer[: self.FRAME_SIZE_BYTES]
            self._buffer = self._buffer[self.FRAME_SIZE_BYTES :]

            # WebRTC processing
            result = self.apm.Process10ms(frame)
            out_frame = result.audio

            processed_output += out_frame

        return processed_output

    @staticmethod
    def to_float(audio_bytes: bytes) -> np.ndarray:
        """Helper to convert processed bytes back to float32 for wake word engines."""
        return np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
