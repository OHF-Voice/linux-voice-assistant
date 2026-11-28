"""Wake word audio: the audio before a wake word, so Home Assistant can verify the wake word (issue #509).

While Home Assistant asks for it, LVA keeps the last 1.5 s of both microphone channels. On a detection it sends the start request
with the size of that audio per channel, the score and the threshold, followed right away by the audio itself and then the live
audio. Home Assistant accepts the wake word (``WAKE_WORD_END``) or rejects it (``WAKE_WORD_REJECTED``).

The behavior follows the ESPHome firmware (esphome/esphome#21885) and the Voice PE (esphome/home-assistant-voice-pe#653). The
functions only decide and build messages. The caller keeps the buffer and sends the messages.
"""

import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

# pylint: disable=no-name-in-module
from aioesphomeapi.api_pb2 import VoiceAssistantAudio, VoiceAssistantRequest  # type: ignore[attr-defined]
from aioesphomeapi.model import VoiceAssistantSubscriptionFlag

from .models import DetectionResult

_LOGGER = logging.getLogger(__name__)

BUFFER_SECONDS = 1.5
"""How much audio before a detection is sent, the default of ESPHome and the Voice PE."""

VERDICT_TIMEOUT_SECONDS = 2.0
"""How long LVA waits for Home Assistant to accept or reject a wake word, the default of ESPHome and the Voice PE."""

ERROR_SHOWN_SECONDS = 1.0
"""How long the error of a verification timeout is shown before LVA goes idle, as on the Voice PE."""

VERIFICATION_TIMEOUT_CODE = "wake-word-verification-timeout"

_BYTES_PER_SAMPLE = 2
_BUFFER_BYTES = int(BUFFER_SECONDS * 16000) * _BYTES_PER_SAMPLE
_SEND_CHUNK_BYTES = 2048
_SILENCE_DBFS = -120.0


@dataclass(frozen=True)
class AudioChunk:
    """One block of microphone audio: ``data`` after WebRTC, ``data2`` before it."""

    data: bytes
    data2: bytes


@dataclass(frozen=True)
class WakeWordAudio:
    """The audio of both channels before a detection, oldest sample first."""

    data: bytes
    data2: bytes


def is_wake_word_audio_requested(flags: Optional[int]) -> bool:
    """
    Whether a voice assistant subscription asks for wake word audio.
    :param flags: ``flags`` of the ``SubscribeVoiceAssistantRequest``, None if the connection is not subscribed.
    """
    return flags is not None and bool(flags & VoiceAssistantSubscriptionFlag.WAKE_WORD_AUDIO)


def buffer_wake_word_audio(buffer: Tuple[AudioChunk, ...], chunk: AudioChunk, buffering: bool) -> Tuple[AudioChunk, ...]:
    """
    Add a block of microphone audio to the buffer, or empty the buffer while it is paused.
    :param buffer: The buffer so far, oldest block first.
    :param chunk: The new block.
    :param buffering: False while the mode is off, the microphone is muted or a pipeline runs.
    :return: The new buffer. It holds at least 1.5 s of each channel once that much was recorded, plus at most one block.

    A paused buffer starts empty when it resumes, so audio from before a pause is never joined to audio after it.
    """
    if not buffering:
        if buffer:
            _LOGGER.debug("Wake word audio buffer paused, %d bytes dropped", sum(len(old.data2) for old in buffer))
        return ()

    if not buffer:
        _LOGGER.debug("Wake word audio buffer started (%.1f s)", BUFFER_SECONDS)

    chunks = buffer + (chunk,)
    while len(chunks) > 1 and _channel_bytes(chunks[1:], 0) >= _BUFFER_BYTES and _channel_bytes(chunks[1:], 1) >= _BUFFER_BYTES:
        chunks = chunks[1:]
    return chunks


def take_wake_word_audio(buffer: Tuple[AudioChunk, ...], wake_word_id: str) -> Optional[WakeWordAudio]:
    """
    The last 1.5 s of each channel in the buffer, for the start request of a detection.
    :param buffer: The buffer up to and including the block with the detection.
    :param wake_word_id: Id of the detected wake word, for the log.
    :return: The audio, or None if one of the channels is empty.

    Each channel is cut on its own, because ``data`` can be a few milliseconds shorter than ``data2`` while WebRTC holds back part
    of a 10 ms frame. The level of both channels is logged, to find out whether ``data2`` is loud enough for the verification.
    """
    data = _last_bytes(b"".join(chunk.data for chunk in buffer))
    data2 = _last_bytes(b"".join(chunk.data2 for chunk in buffer))
    if not data or not data2:
        _LOGGER.debug("No wake word audio for '%s': the buffer is empty", wake_word_id)
        return None

    peak, rms = audio_level(data)
    peak2, rms2 = audio_level(data2)
    _LOGGER.debug(
        "Wake word audio for '%s': data %d bytes (peak %.1f dBFS, RMS %.1f dBFS), data2 %d bytes (peak %.1f dBFS, RMS %.1f dBFS)",
        wake_word_id,
        len(data),
        peak,
        rms,
        len(data2),
        peak2,
        rms2,
    )
    return WakeWordAudio(data, data2)


def start_request(wake_word_phrase: str, detection: DetectionResult, audio: WakeWordAudio) -> VoiceAssistantRequest:
    """
    The start request of a run whose wake word Home Assistant verifies.
    :param wake_word_phrase: Name of the wake word in Home Assistant.
    :param detection: The detection, its id is LVA's id of the wake word.
    :param audio: The audio before the detection, sent right after the request.
    """
    return VoiceAssistantRequest(
        start=True,
        wake_word_phrase=wake_word_phrase,
        wake_word_id=detection.wake_word_id,
        wake_word_audio_bytes=len(audio.data),
        wake_word_audio_bytes2=len(audio.data2),
        wake_word_probability=detection.score or 0.0,
        wake_word_probability_cutoff=detection.threshold or 0.0,
    )


def audio_messages(audio: WakeWordAudio) -> List[VoiceAssistantAudio]:
    """
    The audio before a detection, split into messages that carry both channels.
    :param audio: The audio before the detection.

    Both channels are split into the same number of parts, so no message has an empty channel, which Home Assistant would take as
    the end of the audio.
    """
    samples = min(len(audio.data), len(audio.data2)) // _BYTES_PER_SAMPLE
    count = min(math.ceil(max(len(audio.data), len(audio.data2)) / _SEND_CHUNK_BYTES), samples)
    return [VoiceAssistantAudio(data=_part(audio.data, index, count), data2=_part(audio.data2, index, count)) for index in range(count)]


def audio_level(pcm: bytes) -> Tuple[float, float]:
    """
    Peak and RMS level of 16 bit audio.
    :param pcm: 16 bit little-endian mono audio.
    :return: Peak and RMS in dBFS, -120 for silence.
    """
    samples = np.frombuffer(pcm[: len(pcm) - len(pcm) % _BYTES_PER_SAMPLE], dtype="<i2").astype(np.float64) / 32768.0
    if samples.size == 0:
        return _SILENCE_DBFS, _SILENCE_DBFS
    return _dbfs(float(np.max(np.abs(samples)))), _dbfs(float(np.sqrt(np.mean(samples**2))))


def _channel_bytes(chunks: Tuple[AudioChunk, ...], channel: int) -> int:
    return sum(len(chunk.data if channel == 0 else chunk.data2) for chunk in chunks)


def _last_bytes(pcm: bytes) -> bytes:
    """The last 1.5 s of a channel. The blocks hold whole samples, so the cut is between samples."""
    return pcm[-_BUFFER_BYTES:]


def _part(pcm: bytes, index: int, count: int) -> bytes:
    """Part ``index`` of ``count`` nearly equal parts of a channel, cut between samples."""
    samples = len(pcm) // _BYTES_PER_SAMPLE
    return pcm[(samples * index // count) * _BYTES_PER_SAMPLE : (samples * (index + 1) // count) * _BYTES_PER_SAMPLE]


def _dbfs(value: float) -> float:
    return 20.0 * math.log10(value) if value > 0 else _SILENCE_DBFS
