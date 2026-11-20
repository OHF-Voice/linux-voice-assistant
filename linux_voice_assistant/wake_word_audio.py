"""Rolling buffer of the microphone audio recorded just before a wake word detection."""

from collections import deque
from dataclasses import dataclass
from typing import Deque, Tuple

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2
DEFAULT_BUFFER_SECONDS = 1.5


@dataclass(frozen=True)
class WakeWordAudio:
    """Audio recorded up to and including the block that triggered a wake word detection."""

    chunks: Tuple[Tuple[bytes, bytes], ...]

    @property
    def audio_bytes(self) -> int:
        """Total size of the first channel (after WebRTC) in bytes."""
        return sum(len(chunk) for chunk, _ in self.chunks)

    @property
    def audio_bytes2(self) -> int:
        """Total size of the second channel (before WebRTC) in bytes."""
        return sum(len(chunk2) for _, chunk2 in self.chunks)


class WakeWordAudioBuffer:
    """Keeps the most recent microphone blocks of both streamed channels."""

    def __init__(self, duration_seconds: float = DEFAULT_BUFFER_SECONDS) -> None:
        """
        :param duration_seconds: How much audio to keep, in seconds.
        """
        self._max_bytes = int(duration_seconds * SAMPLE_RATE) * SAMPLE_WIDTH
        self._chunks: Deque[Tuple[bytes, bytes]] = deque()
        self._bytes = 0

    def append(self, chunk: bytes, chunk2: bytes) -> None:
        """
        Add one microphone block and drop the blocks that are no longer needed.
        :param chunk: Block of the first channel (after WebRTC).
        :param chunk2: Block of the second channel (before WebRTC).
        """
        self._chunks.append((chunk, chunk2))
        self._bytes += len(chunk)
        while len(self._chunks) > 1 and self._bytes - len(self._chunks[0][0]) >= self._max_bytes:
            self._bytes -= len(self._chunks.popleft()[0])

    def snapshot(self) -> WakeWordAudio:
        """Return the audio buffered so far, oldest block first."""
        return WakeWordAudio(tuple(self._chunks))

    def clear(self) -> None:
        """Discard all buffered audio."""
        self._chunks.clear()
        self._bytes = 0
