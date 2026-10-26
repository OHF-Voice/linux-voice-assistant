"""Bridge between SendSpin client and MediaPlayer entity with time-synchronized playback.

This module provides time-synchronized audio playback using soundcard with
DAC-level timing precision. It maintains sync with other SendSpin clients through
continuous drift correction via sample drop/insert.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Final, Protocol

import numpy as np
import soundcard as sc
from aioesphomeapi.model import MediaPlayerState
from aiosendspin.client import ClientListener, SendspinClient
from aiosendspin.client.models import PairingSupport
from aiosendspin.models.controller import ControllerStatePayload
from aiosendspin.models.core import DeviceInfo, ServerCommandPayload, StreamStartMessage
from aiosendspin.models.player import ClientHelloPlayerSupport, SupportedAudioFormat
from aiosendspin.models.types import AudioCodec, MediaCommand, PlayerCommand, PlaybackStateType, Roles
from aiosendspin.noise.keys import Identity
from aiosendspin.noise.trust_store import ClientPairingStore, FileClientPairingStore

from linux_voice_assistant.player import state

if TYPE_CHECKING:
    from aiosendspin.client import AudioFormat, PCMFormat

    from .entity import MediaPlayerEntity

_LOGGER = logging.getLogger(__name__)


class AudioTimeInfo(Protocol):
    """Protocol for audio timing information from the soundcard callback."""

    outputBufferDacTime: float  # noqa: N815
    """DAC time when the output buffer will be played (in seconds)."""


class _SoundCardOutputStream:
    """Small soundcard-backed output stream compatible with the existing player API."""

    def __init__(
        self,
        *,
        samplerate: int,
        channels: int,
        dtype: str,
        blocksize: int,
        callback: Callable[[memoryview, int, AudioTimeInfo, object], None],
        latency: str,
        device: int | str | None,
    ) -> None:
        del latency
        self._samplerate = samplerate
        self._channels = channels
        self._dtype = dtype
        self._blocksize = blocksize
        self._callback = callback
        self._device = device
        self._speaker = self._resolve_speaker(device)
        self._thread: threading.Thread | None = None
        self._running = False

    def _resolve_speaker(self, device: int | str | None):
        if isinstance(device, int):
            try:
                speakers = list(sc.all_speakers())
                if 0 <= device < len(speakers):
                    return speakers[device]
            except Exception:
                _LOGGER.debug("Failed to resolve soundcard speaker by index", exc_info=True)

        if isinstance(device, str):
            try:
                return sc.get_speaker(device)
            except Exception:
                _LOGGER.debug("Failed to resolve soundcard speaker by name %s", device, exc_info=True)

        default = getattr(sc, "default", None)
        if default is not None:
            try:
                return default.speaker
            except Exception:
                pass

        speakers = list(sc.all_speakers())
        if speakers:
            return speakers[0]

        raise RuntimeError("No soundcard speaker available")

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            with self._speaker.player(
                samplerate=self._samplerate,
                channels=self._channels,
                blocksize=self._blocksize,
            ) as player:
                while self._running:
                    frames = self._blocksize
                    sample_bytes = np.dtype(self._dtype).itemsize * self._channels
                    outdata = bytearray(frames * sample_bytes)
                    time_info = type("_CallbackTimeInfo", (), {"outputBufferDacTime": time.monotonic()})()
                    status = None
                    try:
                        self._callback(memoryview(outdata), frames, time_info, status)
                    except Exception:
                        _LOGGER.exception("Error in soundcard output callback")
                        outdata = b"\x00" * len(outdata)
                    try:
                        pcm_int16 = np.frombuffer(outdata, dtype=np.int16)
                        pcm_float = pcm_int16.astype(np.float32) / 32768.0
                        if self._channels == 1:
                            player.play(pcm_float)
                        else:
                            player.play(pcm_float.reshape(-1, self._channels))
                    except Exception:
                        _LOGGER.exception("Failed to play audio via soundcard")
                        break
        except Exception:
            _LOGGER.exception("Failed to open soundcard player stream")

    def stop(self) -> None:
        self._running = False
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=0.25)

    def close(self) -> None:
        self.stop()


class PlaybackState(Enum):
    """State machine for audio playback lifecycle."""

    INITIALIZING = auto()
    """Waiting for first audio chunk and sync info."""

    WAITING_FOR_START = auto()
    """Buffer filled, scheduled start time computed, awaiting start gate."""

    PLAYING = auto()
    """Audio actively playing with sync corrections."""


@dataclass
class _QueuedChunk:
    """Represents a queued audio chunk with timing information."""

    server_timestamp_us: int
    """Server timestamp when this chunk should start playing."""
    audio_data: bytes
    """Raw PCM audio bytes."""


class _SyncErrorFilter:
    """Simple exponential moving average filter for sync error smoothing."""

    def __init__(self, alpha: float = 0.1) -> None:
        self._alpha = alpha
        self._value: float = 0.0
        self._initialized = False

    @property
    def offset(self) -> float:
        return self._value

    @property
    def is_synchronized(self) -> bool:
        return self._initialized

    def update(self, measurement: int, max_error: int, time_added: int) -> None:
        """Update filter with new measurement."""
        del max_error, time_added  # Unused, kept for API compatibility
        if not self._initialized:
            self._value = float(measurement)
            self._initialized = True
        else:
            self._value = self._alpha * measurement + (1 - self._alpha) * self._value

    def reset(self) -> None:
        self._value = 0.0
        self._initialized = False


class AudioPlayer:
    """Audio player with time synchronization support using sounddevice.

    This player accepts audio chunks with server timestamps and dynamically
    computes playback times using time synchronization functions. It maintains
    sync through DAC timing calibration and continuous drift correction.
    """

    _loop: asyncio.AbstractEventLoop
    _compute_client_time: Callable[[int], int]
    _compute_server_time: Callable[[int], int]

    # Constants
    _MICROSECONDS_PER_SECOND: Final[int] = 1_000_000
    _DAC_PER_LOOP_MIN: Final[float] = 0.999
    _DAC_PER_LOOP_MAX: Final[float] = 1.001

    # Sync error correction
    _MAX_SPEED_CORRECTION: Final[float] = 0.04  # ±4%
    _CORRECTION_DEADBAND_US: Final[int] = 2_000  # 2ms
    _REANCHOR_THRESHOLD_US: Final[int] = 500_000  # 500ms
    _REANCHOR_COOLDOWN_US: Final[int] = 5_000_000  # 5s
    _CORRECTION_TARGET_SECONDS: Final[float] = 2.0

    # Audio stream configuration
    _BLOCKSIZE: Final[int] = 2048  # ~46ms at 44.1kHz

    # Time synchronization thresholds
    _EARLY_START_THRESHOLD_US: Final[int] = 700_000  # 700ms
    _START_TIME_UPDATE_THRESHOLD_US: Final[int] = 5_000  # 5ms

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        compute_client_time: Callable[[int], int],
        compute_server_time: Callable[[int], int],
        device: int | str | None = None,
    ) -> None:
        """Initialize the audio player.

        Args:
            loop: The asyncio event loop to use for scheduling.
            compute_client_time: Function that converts server timestamps to client
                timestamps (monotonic loop time).
            compute_server_time: Function that converts client timestamps to server
                timestamps.
            device: Audio device index or name (None for default).
        """
        self._loop = loop
        self._compute_client_time = compute_client_time
        self._compute_server_time = compute_server_time
        self._device = device
        self._format: PCMFormat | None = None
        self._queue: asyncio.Queue[_QueuedChunk] = asyncio.Queue()
        self._stream: _SoundCardOutputStream | None = None
        self._closed = False
        self._stream_started = False

        self._volume: int = 60
        self._muted: bool = False

        # Partial chunk tracking
        self._current_chunk: _QueuedChunk | None = None
        self._current_chunk_offset = 0

        # Track expected next chunk timestamp for gap/overlap handling
        self._expected_next_timestamp: int | None = None

        # Track queued audio duration
        self._queued_duration_us = 0

        # DAC timing for accurate playback position tracking
        self._dac_loop_calibrations: collections.deque[tuple[int, int]] = collections.deque(maxlen=100)
        self._last_known_playback_position_us: int = 0
        self._last_dac_calibration_time_us: int = 0

        # Playback state machine
        self._playback_state: PlaybackState = PlaybackState.INITIALIZING

        # Scheduled start anchoring
        self._scheduled_start_loop_time_us: int | None = None
        self._scheduled_start_dac_time_us: int | None = None

        # Server timeline cursor for input frames
        self._server_ts_cursor_us: int = 0
        self._server_ts_cursor_remainder: int = 0

        # First-chunk and re-anchor tracking
        self._first_server_timestamp_us: int | None = None
        self._early_start_suspect: bool = False
        self._has_reanchored: bool = False

        # Drift correction scheduling (sample drop/insert)
        self._insert_every_n_frames: int = 0
        self._drop_every_n_frames: int = 0
        self._frames_until_next_insert: int = 0
        self._frames_until_next_drop: int = 0
        self._last_output_frame: bytes = b""

        # Sync error smoothing and re-anchor cooldown
        self._sync_error_filter = _SyncErrorFilter(alpha=0.1)
        self._sync_error_filtered_us: float = 0.0
        self._last_reanchor_loop_time_us: int = 0
        self._last_sync_error_log_us: int = 0
        self._frames_inserted_since_log: int = 0
        self._frames_dropped_since_log: int = 0

        # Thread-safe flag for deferred operations
        self._clear_requested: bool = False

    def set_format(self, audio_format: "AudioFormat") -> None:
        """Configure the audio output format.

        Args:
            audio_format: Audio format specification from SendSpin.
        """
        pcm_format = audio_format.pcm_format
        self._format = pcm_format
        self._close_stream()

        # Reset state on format change
        self._stream_started = False

        # Create a soundcard-backed output stream with the existing callback behavior.
        self._stream = _SoundCardOutputStream(
            samplerate=pcm_format.sample_rate,
            channels=pcm_format.channels,
            dtype="int16",
            blocksize=self._BLOCKSIZE,
            callback=self._audio_callback,
            latency="high",
            device=self._device,
        )
        _LOGGER.info(
            "Audio stream configured: %d Hz, %d ch, blocksize=%d, device=%s",
            pcm_format.sample_rate,
            pcm_format.channels,
            self._BLOCKSIZE,
            self._device,
        )

    @property
    def volume(self) -> int:
        return self._volume

    @property
    def muted(self) -> bool:
        return self._muted

    def set_volume(self, volume: int, *, muted: bool) -> None:
        """Set player volume and mute state."""
        self._volume = max(0, min(100, volume))
        self._muted = muted

    async def stop(self) -> None:
        """Stop playback and release resources."""
        self._closed = True
        self._close_stream()

    def clear(self) -> None:
        """Drop all queued audio chunks and reset state."""
        self._clear_requested = False

        # Stop stream but don't close it
        if self._stream is not None and self._stream_started:
            try:
                self._stream.stop()
            except Exception:
                _LOGGER.debug("Failed to stop audio stream on clear", exc_info=True)
        self._stream_started = False

        # Drain queue
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        # Reset all state
        self._playback_state = PlaybackState.INITIALIZING
        self._current_chunk = None
        self._current_chunk_offset = 0
        self._expected_next_timestamp = None
        self._queued_duration_us = 0
        self._dac_loop_calibrations.clear()
        self._last_known_playback_position_us = 0
        self._last_dac_calibration_time_us = 0
        self._scheduled_start_loop_time_us = None
        self._scheduled_start_dac_time_us = None
        self._server_ts_cursor_us = 0
        self._server_ts_cursor_remainder = 0
        self._first_server_timestamp_us = None
        self._early_start_suspect = False
        self._has_reanchored = False
        self._insert_every_n_frames = 0
        self._drop_every_n_frames = 0
        self._frames_until_next_insert = 0
        self._frames_until_next_drop = 0
        self._last_output_frame = b""
        self._sync_error_filter.reset()
        self._sync_error_filtered_us = 0.0
        self._last_reanchor_loop_time_us = 0
        self._last_sync_error_log_us = 0
        self._frames_inserted_since_log = 0
        self._frames_dropped_since_log = 0

    def _audio_callback(self, outdata, frames, time, status):
        if self._format is None:
            return

        bytes_needed = frames * self._format.frame_size
        output_buffer = memoryview(outdata).cast("B")

        if status is not None:
            input_underflow = bool(getattr(status, "input_underflow", False))
            output_underflow = bool(getattr(status, "output_underflow", False))
            if input_underflow or output_underflow:
                _LOGGER.warning("Audio underflow detected; clearing current queue")
                self._clear_requested = True
                self._fill_silence(output_buffer, 0, bytes_needed)
                return

        self._update_playback_position_from_dac(time)

        bytes_written = 0
        if self._playback_state == PlaybackState.WAITING_FOR_START:
            bytes_written = self._handle_start_gating(output_buffer, 0, frames, time)

        if self._playback_state == PlaybackState.PLAYING and bytes_written < bytes_needed:
            try:
                frames_remaining = (bytes_needed - bytes_written) // self._format.frame_size
                pcm_data = self._read_input_frames_bulk(frames_remaining)
                if len(pcm_data) < (bytes_needed - bytes_written):
                    output_buffer[bytes_written : bytes_written + len(pcm_data)] = pcm_data
                    self._fill_silence(output_buffer, bytes_written + len(pcm_data), bytes_needed - bytes_written - len(pcm_data))
                else:
                    output_buffer[bytes_written:bytes_needed] = pcm_data[: bytes_needed - bytes_written]
            except Exception:
                _LOGGER.exception("Error in audio callback")
                self._fill_silence(output_buffer, bytes_written, bytes_needed - bytes_written)
                self._current_chunk = None
                self._current_chunk_offset = 0
        elif bytes_written < bytes_needed:
            # Still waiting for start and gating didn't fill the whole buffer
            self._fill_silence(output_buffer, bytes_written, bytes_needed - bytes_written)

        self._apply_volume(output_buffer, bytes_needed)

    def _update_playback_position_from_dac(self, time: AudioTimeInfo) -> None:
        """Capture DAC and loop time simultaneously, update playback position."""
        try:
            dac_time_us = int(time.outputBufferDacTime * self._MICROSECONDS_PER_SECOND)
            loop_time_us = int(self._loop.time() * self._MICROSECONDS_PER_SECOND)

            self._dac_loop_calibrations.append((dac_time_us, loop_time_us))
            self._last_dac_calibration_time_us = loop_time_us

            try:
                loop_at_dac_us = self._estimate_loop_time_for_dac_time(dac_time_us)
                if loop_at_dac_us == 0:
                    loop_at_dac_us = loop_time_us
                estimated_position = self._compute_server_time(loop_at_dac_us)
                self._last_known_playback_position_us = estimated_position
            except Exception:
                _LOGGER.debug("Failed to estimate playback position", exc_info=True)

            if self._scheduled_start_dac_time_us is None and self._scheduled_start_loop_time_us:
                try:
                    loop_start = self._scheduled_start_loop_time_us
                    est_dac = self._estimate_dac_time_for_server_timestamp(self._compute_server_time(loop_start))
                    if est_dac:
                        self._scheduled_start_dac_time_us = est_dac
                except Exception:
                    _LOGGER.debug("Failed to estimate DAC start time", exc_info=True)
                    self._scheduled_start_dac_time_us = self._scheduled_start_loop_time_us

        except (AttributeError, TypeError):
            _LOGGER.debug("Could not extract timing info from callback")

    def _initialize_current_chunk(self) -> None:
        """Load next chunk from queue."""
        self._current_chunk = self._queue.get_nowait()
        self._current_chunk_offset = 0
        if self._server_ts_cursor_us == 0:
            self._server_ts_cursor_us = self._current_chunk.server_timestamp_us

    def _read_one_input_frame(self) -> bytes | None:
        """Read and consume a single audio frame from the queue."""
        if self._format is None or self._format.frame_size == 0:
            return None

        frame_size = self._format.frame_size

        if self._current_chunk is None:
            if self._queue.empty():
                return None
            self._initialize_current_chunk()

        chunk = self._current_chunk
        assert chunk is not None
        data = chunk.audio_data
        if self._current_chunk_offset >= len(data):
            self._advance_finished_chunk()
            return None

        start = self._current_chunk_offset
        end = start + frame_size
        end = min(end, len(data))
        frame = data[start:end]

        self._current_chunk_offset = end
        self._advance_server_cursor_frames(1)

        if self._current_chunk_offset >= len(data):
            self._advance_finished_chunk()

        if len(frame) < frame_size:
            frame = frame + b"\x00" * (frame_size - len(frame))
        return frame

    def _read_input_frames_bulk(self, n_frames: int) -> bytes:
        """Read N frames efficiently in bulk."""
        if self._format is None or n_frames <= 0:
            return b""

        frame_size = self._format.frame_size
        total_bytes_needed = n_frames * frame_size
        result = bytearray(total_bytes_needed)
        bytes_written = 0

        while bytes_written < total_bytes_needed:
            if self._current_chunk is None:
                if self._queue.empty():
                    silence_bytes = total_bytes_needed - bytes_written
                    result[bytes_written:] = b"\x00" * silence_bytes
                    break
                self._initialize_current_chunk()

            assert self._current_chunk is not None
            chunk_data = self._current_chunk.audio_data
            available_bytes = len(chunk_data) - self._current_chunk_offset
            bytes_to_read = min(available_bytes, total_bytes_needed - bytes_written)

            result[bytes_written : bytes_written + bytes_to_read] = chunk_data[self._current_chunk_offset : self._current_chunk_offset + bytes_to_read]

            self._current_chunk_offset += bytes_to_read
            bytes_written += bytes_to_read
            frames_read = bytes_to_read // frame_size
            self._advance_server_cursor_frames(frames_read)

            if self._current_chunk_offset >= len(chunk_data):
                self._advance_finished_chunk()

        if bytes_written >= frame_size:
            self._last_output_frame = bytes(result[bytes_written - frame_size : bytes_written])

        return bytes(result)

    def _advance_finished_chunk(self) -> None:
        """Update durations when current chunk is fully consumed."""
        if self._format is None or self._current_chunk is None:
            return
        data = self._current_chunk.audio_data
        chunk_frames = len(data) // self._format.frame_size
        chunk_duration_us = (chunk_frames * self._MICROSECONDS_PER_SECOND) // self._format.sample_rate
        self._queued_duration_us = max(0, self._queued_duration_us - chunk_duration_us)
        self._current_chunk = None
        self._current_chunk_offset = 0

    def _advance_server_cursor_frames(self, frames: int) -> None:
        """Advance server timeline cursor by frames."""
        if self._format is None or frames <= 0:
            return
        self._server_ts_cursor_remainder += frames * self._MICROSECONDS_PER_SECOND
        sr = self._format.sample_rate
        if self._server_ts_cursor_remainder >= sr:
            inc_us = self._server_ts_cursor_remainder // sr
            self._server_ts_cursor_remainder = self._server_ts_cursor_remainder % sr
            self._server_ts_cursor_us += int(inc_us)

    def _skip_input_frames(self, frames_to_skip: int) -> None:
        """Discard frames from input to reduce buffer depth quickly."""
        if self._format is None or frames_to_skip <= 0:
            return
        frame_size = self._format.frame_size
        while frames_to_skip > 0:
            if self._current_chunk is None:
                if self._queue.empty():
                    break
                self._current_chunk = self._queue.get_nowait()
                self._current_chunk_offset = 0
                if self._server_ts_cursor_us == 0:
                    self._server_ts_cursor_us = self._current_chunk.server_timestamp_us
            data = self._current_chunk.audio_data
            rem_bytes = len(data) - self._current_chunk_offset
            rem_frames = rem_bytes // frame_size
            if rem_frames <= 0:
                self._advance_finished_chunk()
                continue
            take = min(rem_frames, frames_to_skip)
            self._current_chunk_offset += take * frame_size
            self._advance_server_cursor_frames(take)
            frames_to_skip -= take
            if self._current_chunk_offset >= len(data):
                self._advance_finished_chunk()

    def _estimate_dac_time_for_server_timestamp(self, server_timestamp_us: int) -> int:
        """Estimate when a server timestamp will play out (in DAC time)."""
        if self._last_dac_calibration_time_us == 0:
            return 0

        loop_time_us = self._compute_client_time(server_timestamp_us)

        if not self._dac_loop_calibrations:
            return 0

        dac_ref_us, loop_ref_us = self._dac_loop_calibrations[-1]
        dac_prev_us, loop_prev_us = (0, 0)
        if len(self._dac_loop_calibrations) >= 2:
            dac_prev_us, loop_prev_us = self._dac_loop_calibrations[-2]

        if loop_ref_us == 0:
            return 0

        dac_per_loop = 1.0
        if loop_prev_us and dac_prev_us and (loop_ref_us != loop_prev_us):
            dac_per_loop = (dac_ref_us - dac_prev_us) / (loop_ref_us - loop_prev_us)
            dac_per_loop = max(self._DAC_PER_LOOP_MIN, min(self._DAC_PER_LOOP_MAX, dac_per_loop))

        return round(dac_ref_us + (loop_time_us - loop_ref_us) * dac_per_loop)

    def _estimate_loop_time_for_dac_time(self, dac_time_us: int) -> int:
        """Estimate loop time corresponding to a DAC time."""
        if not self._dac_loop_calibrations:
            return 0
        dac_ref_us, loop_ref_us = self._dac_loop_calibrations[-1]
        if loop_ref_us == 0:
            return 0
        dac_prev_us, loop_prev_us = (0, 0)
        if len(self._dac_loop_calibrations) >= 2:
            dac_prev_us, loop_prev_us = self._dac_loop_calibrations[-2]
        loop_per_dac = 1.0
        if dac_prev_us and (dac_ref_us != dac_prev_us):
            loop_per_dac = (loop_ref_us - loop_prev_us) / (dac_ref_us - dac_prev_us)
            loop_per_dac = max(self._DAC_PER_LOOP_MIN, min(self._DAC_PER_LOOP_MAX, loop_per_dac))
        return round(loop_ref_us + (dac_time_us - dac_ref_us) * loop_per_dac)

    def _smooth_sync_error(self, error_us: int) -> None:
        """Update filtered sync error."""
        now_us = int(self._loop.time() * self._MICROSECONDS_PER_SECOND)
        max_error_us = 5_000
        self._sync_error_filter.update(
            measurement=error_us,
            max_error=max_error_us,
            time_added=now_us,
        )
        self._sync_error_filtered_us = self._sync_error_filter.offset

    def _fill_silence(self, output_buffer: memoryview, offset: int, num_bytes: int) -> None:
        """Fill output buffer range with silence."""
        if num_bytes > 0:
            output_buffer[offset : offset + num_bytes] = b"\x00" * num_bytes

    def _apply_volume(self, output_buffer: memoryview, num_bytes: int) -> None:
        """Apply volume scaling to output buffer."""
        muted = self._muted
        volume = self._volume

        if muted or volume == 0:
            output_buffer[:num_bytes] = b"\x00" * num_bytes
            return

        if volume == 100:
            return

        samples = np.frombuffer(output_buffer[:num_bytes], dtype=np.int16).copy()
        # Cubic curve matches mpv's gain = pow(volume/100, 3) so that
        # the same 0-100 value produces the same perceived loudness on
        # both the SendSpin PCM path and the MPV path.
        amplitude = (volume / 100.0) ** 3
        samples = (samples * amplitude).astype(np.int16)
        output_buffer[:num_bytes] = samples.tobytes()

    def _compute_and_set_loop_start(self, server_timestamp_us: int) -> None:
        """Compute and set scheduled start time from server timestamp."""
        try:
            self._scheduled_start_loop_time_us = self._compute_client_time(server_timestamp_us)
        except Exception:
            _LOGGER.exception("Failed to compute client time for start")
            self._scheduled_start_loop_time_us = int(self._loop.time() * self._MICROSECONDS_PER_SECOND)

    def _handle_start_gating(
        self,
        output_buffer: memoryview,
        bytes_written: int,
        frames: int,
        time: AudioTimeInfo | None = None,
    ) -> int:
        """Handle pre-start gating using DAC or loop time."""
        assert self._format is not None

        use_dac_gating = False
        dac_now_us = 0
        if time is not None and self._scheduled_start_dac_time_us is not None:
            try:
                dac_now_us = int(time.outputBufferDacTime * self._MICROSECONDS_PER_SECOND)
                if dac_now_us > 0:
                    use_dac_gating = True
            except (AttributeError, TypeError):
                pass

        if use_dac_gating:
            assert self._scheduled_start_dac_time_us is not None
            delta_us = self._scheduled_start_dac_time_us - dac_now_us
            target_time_us = self._scheduled_start_dac_time_us
            current_time_us = dac_now_us
            can_drop_frames = True
        elif self._scheduled_start_loop_time_us is not None:
            loop_now_us = int(self._loop.time() * self._MICROSECONDS_PER_SECOND)
            delta_us = self._scheduled_start_loop_time_us - loop_now_us
            target_time_us = self._scheduled_start_loop_time_us
            current_time_us = loop_now_us
            can_drop_frames = False
        else:
            return bytes_written

        if delta_us > 0:
            frames_until_start = int((delta_us * self._format.sample_rate + 999_999) // self._MICROSECONDS_PER_SECOND)
            frames_to_silence = min(frames_until_start, frames)
            silence_bytes = frames_to_silence * self._format.frame_size
            self._fill_silence(output_buffer, bytes_written, silence_bytes)
            bytes_written += silence_bytes
        elif delta_us < 0 and can_drop_frames:
            if not (self._early_start_suspect and not self._has_reanchored):
                frames_to_drop = int(((-delta_us) * self._format.sample_rate + 999_999) // self._MICROSECONDS_PER_SECOND)
                self._skip_input_frames(frames_to_drop)
                self._playback_state = PlaybackState.PLAYING

        if current_time_us >= target_time_us:
            self._playback_state = PlaybackState.PLAYING

        return bytes_written

    def _update_correction_schedule(self, error_us: int) -> None:
        """Keep the SendSpin output path stable by avoiding frame insertion/drop logic."""
        if self._format is None or self._format.sample_rate <= 0:
            return

        # The custom sample drop/insert path is currently more harmful than helpful:
        # tiny timestamp jitter and the synthetic callback timing create audible
        # chopping, so keep the stream as a simple PCM pass-through.
        self._insert_every_n_frames = 0
        self._drop_every_n_frames = 0
        self._frames_until_next_insert = 0
        self._frames_until_next_drop = 0
        self._last_output_frame = b""
        self._sync_error_filtered_us = 0.0
        self._sync_error_filter.reset()
        del error_us
        return

    def _log_sync_status(self) -> None:
        """Log sync error and buffer status periodically."""
        if not self._sync_error_filter.is_synchronized:
            return
        now_us = int(self._loop.time() * self._MICROSECONDS_PER_SECOND)
        if now_us - self._last_sync_error_log_us >= self._MICROSECONDS_PER_SECOND:
            self._last_sync_error_log_us = now_us
            if self._format is not None:
                expected_frames = self._format.sample_rate
                track_frames = expected_frames + self._frames_dropped_since_log - self._frames_inserted_since_log
                playback_speed_percent = (track_frames / expected_frames) * 100.0
            else:
                playback_speed_percent = 100.0

            _LOGGER.debug(
                "Sync error: %.1f ms, buffer: %.2f s, speed: %.2f%%, " "inserted: %d, dropped: %d",
                self._sync_error_filtered_us / 1000.0,
                self._queued_duration_us / self._MICROSECONDS_PER_SECOND,
                playback_speed_percent,
                self._frames_inserted_since_log,
                self._frames_dropped_since_log,
            )
            self._frames_inserted_since_log = 0
            self._frames_dropped_since_log = 0

    def submit(self, server_timestamp_us: int, payload: bytes) -> None:
        """Queue an audio payload for playback.

        Args:
            server_timestamp_us: Server timestamp when this audio should play.
            payload: Raw PCM audio bytes.
        """
        # Handle deferred operations from audio thread
        if self._clear_requested:
            self._clear_requested = False
            self.clear()
            _LOGGER.info("Cleared audio queue after underflow")

        if self._format is None:
            _LOGGER.debug("Audio format missing; dropping audio chunk")
            return
        if self._format.frame_size == 0:
            return
        if len(payload) % self._format.frame_size != 0:
            _LOGGER.warning(
                "Dropping audio chunk with invalid size: %s bytes (frame size %s)",
                len(payload),
                self._format.frame_size,
            )
            return

        now_us = int(self._loop.time() * self._MICROSECONDS_PER_SECOND)

        # Anchor playback start to the server-scheduled time (computed via the
        # clock-sync offset), so this client starts in sync with other Sendspin
        # players instead of racing ahead by however long local buffering took.
        if self._scheduled_start_loop_time_us is None:
            try:
                self._scheduled_start_loop_time_us = self._compute_client_time(server_timestamp_us)
            except Exception:
                _LOGGER.exception("Failed to compute client time for start; starting immediately")
                self._scheduled_start_loop_time_us = int(self._loop.time() * self._MICROSECONDS_PER_SECOND)
            self._scheduled_start_dac_time_us = None
            self._playback_state = PlaybackState.WAITING_FOR_START
            self._first_server_timestamp_us = server_timestamp_us
            now_us = int(self._loop.time() * self._MICROSECONDS_PER_SECOND)
            self._early_start_suspect = (self._scheduled_start_loop_time_us - now_us) < -self._EARLY_START_THRESHOLD_US

        # While waiting to start, update scheduled start as time sync improves
        elif self._playback_state == PlaybackState.WAITING_FOR_START and self._first_server_timestamp_us is not None:
            try:
                updated_loop_start = self._compute_client_time(self._first_server_timestamp_us)
                if abs(updated_loop_start - (self._scheduled_start_loop_time_us or 0)) > self._START_TIME_UPDATE_THRESHOLD_US:
                    self._scheduled_start_loop_time_us = updated_loop_start
                    est_dac = self._estimate_dac_time_for_server_timestamp(self._first_server_timestamp_us)
                    self._scheduled_start_dac_time_us = est_dac if est_dac else None
            except Exception:
                _LOGGER.debug("Failed to update start time", exc_info=True)

        # Compute sync error and schedule corrections when playing
        if self._playback_state == PlaybackState.PLAYING and self._last_known_playback_position_us > 0 and self._server_ts_cursor_us > 0:
            sync_error_us = self._last_known_playback_position_us - self._server_ts_cursor_us
            self._update_correction_schedule(sync_error_us)

        self._log_sync_status()

        # Initialize expected timestamp on first chunk
        if self._expected_next_timestamp is None:
            self._expected_next_timestamp = server_timestamp_us
        # Handle real gaps only; tiny timestamp jitter should not trigger silence insertion.
        elif server_timestamp_us > self._expected_next_timestamp:
            gap_us = server_timestamp_us - self._expected_next_timestamp
            if gap_us <= 1000:
                self._expected_next_timestamp = server_timestamp_us
            else:
                gap_frames = (gap_us * self._format.sample_rate) // self._MICROSECONDS_PER_SECOND
                silence_bytes = gap_frames * self._format.frame_size
                silence = b"\x00" * silence_bytes
                self._queue.put_nowait(
                    _QueuedChunk(
                        server_timestamp_us=self._expected_next_timestamp,
                        audio_data=silence,
                    )
                )
                silence_duration_us = (gap_frames * self._MICROSECONDS_PER_SECOND) // self._format.sample_rate
                self._queued_duration_us += silence_duration_us
                _LOGGER.debug("Gap: %.1f ms filled with silence", gap_us / 1000.0)
                self._expected_next_timestamp = server_timestamp_us

        # Handle overlap: trim the start
        elif server_timestamp_us < self._expected_next_timestamp:
            overlap_us = self._expected_next_timestamp - server_timestamp_us
            overlap_frames = (overlap_us * self._format.sample_rate) // self._MICROSECONDS_PER_SECOND
            trim_bytes = overlap_frames * self._format.frame_size
            if trim_bytes < len(payload):
                payload = payload[trim_bytes:]
                server_timestamp_us = self._expected_next_timestamp
                _LOGGER.debug("Overlap: %.1f ms trimmed", overlap_us / 1000.0)
            else:
                _LOGGER.debug("Overlap: %.1f ms (chunk skipped)", overlap_us / 1000.0)
                return

        # Queue the chunk
        if len(payload) > 0:
            chunk_frames = len(payload) // self._format.frame_size
            chunk_duration_us = (chunk_frames * self._MICROSECONDS_PER_SECOND) // self._format.sample_rate
            chunk = _QueuedChunk(
                server_timestamp_us=server_timestamp_us,
                audio_data=payload,
            )
            self._queue.put_nowait(chunk)
            self._queued_duration_us += chunk_duration_us
            self._expected_next_timestamp = server_timestamp_us + chunk_duration_us

        # Start stream when first chunk arrives
        if not self._stream_started and self._queue.qsize() > 0 and self._stream is not None:
            self._stream.start()
            self._stream_started = True
            _LOGGER.info("SendSpin bridge started (no server URL - waiting for connections)")

    def _close_stream(self) -> None:
        """Close the audio output stream."""
        stream = self._stream
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                _LOGGER.debug("Failed to close audio output stream", exc_info=True)
        self._stream = None


class SendspinBridge:
    """Bridge that connects SendSpin streaming to MediaPlayerEntity.

    Uses time-synchronized audio playback with DAC timing and drift correction
    to stay in sync with other SendSpin clients.
    """

    def __init__(
        self,
        media_player_entity: "MediaPlayerEntity",
        client_id: str | None = None,
        client_name: str | None = None,
        static_delay_ms: float = 0.0,
        audio_device: str | int | None = None,
    ) -> None:
        """Initialize the SendSpin bridge.

        Args:
            media_player_entity: MediaPlayerEntity to update state
            client_id: Unique client ID
            client_name: Friendly client name
            static_delay_ms: Static playback delay
            audio_device: Audio device index or name
        """
        self.media_player = media_player_entity

        lva_name: str | None = None
        state = getattr(media_player_entity.server, "state", None)
        if state is not None:
            lva_name = getattr(state, "friendly_name", None) or getattr(state, "name", None)

        self.client_id = client_id or f"linux-voice-assistant-{lva_name}"
        self.client_name = client_name
        self._audio_device = audio_device
        self._static_delay_ms = static_delay_ms

        self._identity_file = Path(f"/app/configuration/sendspin_identity_{self.client_id}.key")
        self._pairing_file = Path(f"/app/configuration/sendspin_pairing_{self.client_id}.json")
        self._identity: Identity | None = None
        self._pairing_store: ClientPairingStore | None = None
        self._client: SendspinClient | None = None
        self._listener: ClientListener | None = None
        self._controller_supported_commands: set[MediaCommand] = set()
        self._running = False
        self._stream_active = False
        self._paused = False  # Track if playback is paused (for announcements)
        self._sendspin_owns_playback: bool = False  # True once Sendspin has played; cleared when HA takes over

        # AudioPlayer for time-synchronized playback
        self._player: AudioPlayer | None = None
        self._current_format: "AudioFormat | None" = None

        # Volume state (synced with MediaPlayerEntity)
        self._volume: int = 100
        self._muted: bool = False
        self._is_ducked: bool = False
        self._pre_mute_volume: int = 100

        # Callback to notify when SendSpin starts playing
        self._on_sendspin_start: Callable[[], None] | None = None

    async def _ensure_identity_and_store(self) -> None:
        if self._identity_file.exists():
            self._identity = Identity.from_private_bytes(self._identity_file.read_bytes())
        else:
            self._identity = Identity.generate()
            self._identity_file.parent.mkdir(parents=True, exist_ok=True)
            self._identity_file.write_bytes(self._identity.private_bytes)
        self._pairing_store = await FileClientPairingStore.open(self._pairing_file)

    async def _on_pairing_pin_display(self, pin: str | None) -> None:
        if pin is not None:
            _LOGGER.info("=" * 60)
            _LOGGER.info("SENDSPIN PAIRING REQUIRED — enter this PIN in Music Assistant: %s", pin)
            _LOGGER.info("=" * 60)
        else:
            _LOGGER.info("SendSpin pairing window closed")

    def _create_client(self) -> SendspinClient:
        assert self._identity is not None and self._pairing_store is not None
        return SendspinClient(
            identity=self._identity,
            client_name=self.client_name,
            roles=[Roles.PLAYER, Roles.CONTROLLER],
            pairing_store=self._pairing_store,
            pairing_support=PairingSupport(
                pin_display=self._on_pairing_pin_display,
                offer_static_pin=True,
            ),
            device_info=DeviceInfo(
                product_name="Linux Voice Assistant",
                manufacturer="Open Home Foundation",
                software_version="1.1.15",
            ),
            player_support=ClientHelloPlayerSupport(
                supported_formats=[
                    SupportedAudioFormat(codec=AudioCodec.PCM, channels=2, sample_rate=44_100, bit_depth=16),
                    SupportedAudioFormat(codec=AudioCodec.PCM, channels=1, sample_rate=44_100, bit_depth=16),
                ],
                buffer_capacity=32_000_000,
                supported_commands=[PlayerCommand.VOLUME, PlayerCommand.MUTE],
            ),
            static_delay_ms=self._static_delay_ms,
        )

    def _on_controller_state(self, payload) -> None:
        # aiosendspin 9.x passes the full ServerStatePayload; the controller
        # data lives under .controller (which can be None on unrelated updates).
        controller = getattr(payload, "controller", None) or payload
        commands = getattr(controller, "supported_commands", None)
        if commands is None:
            return
        self._controller_supported_commands = set(commands)
        _LOGGER.info(
            "Sendspin controller state received: supported=%s",
            sorted(str(c) for c in self._controller_supported_commands),
        )

    async def _send_group_command(self, command: MediaCommand) -> None:
        if not self._client or not self._client.connected:
            _LOGGER.warning("Sendspin group command %s skipped: not connected", command)
            return
        if command not in self._controller_supported_commands:
            _LOGGER.warning(
                "Sendspin group command %s skipped: not in supported set %s",
                command,
                sorted(str(c) for c in self._controller_supported_commands),
            )
            return
        try:
            await self._client.send_group_command(command)
            _LOGGER.info("Sendspin group command %s sent", command)
        except ValueError:
            _LOGGER.warning("Sendspin group command %s rejected", command, exc_info=True)

    def release_playback(self) -> None:
        self._sendspin_owns_playback = False
        self._paused = False

    def set_on_sendspin_start(self, callback: Callable[[], None]) -> None:
        """Set callback to be called when SendSpin starts playing."""
        self._on_sendspin_start = callback

    def set_volume(self, volume: int, muted: bool = False) -> None:
        """Set volume (called from MediaPlayerEntity when HA changes volume)."""
        self._volume = max(0, min(100, volume))
        self._muted = muted
        self._update_player_volume()

        # Report volume to SendSpin server (same 0-100 scale, no conversion)
        if self._client and self._client.connected:
            asyncio.get_event_loop().call_soon(
                lambda v=self._volume: asyncio.create_task(
                    self._client.send_player_state(
                        available=True,
                        volume=v,
                        muted=self._muted,
                    )
                )
            )

    def duck(self) -> None:
        """Reduce volume (for wake word/TTS)."""
        self._is_ducked = True
        self._update_player_volume()

    def unduck(self) -> None:
        """Restore volume (after wake word/TTS)."""
        self._is_ducked = False
        if not self._paused:
            self._update_player_volume()

    def _update_player_volume(self) -> None:
        """Apply current volume (with duck state) to the audio player.

        Ducking halves the volume value before the cubic curve, matching
        MPV's duck behavior (LibMpvPlayer.duck(0.5) halves mpv.volume).
        """
        vol = self._volume
        if self._is_ducked:
            vol = vol // 2
        if self._player:
            self._player.set_volume(vol, muted=self._muted)

    def pause(self) -> None:
        if self._stream_active and not self._paused:
            self._paused = True
            if self._player:
                self._player.set_volume(0, muted=True)
            if self._client and self._client.connected:
                asyncio.get_event_loop().call_soon(lambda: asyncio.create_task(self._send_group_command(MediaCommand.PAUSE)))
            _LOGGER.info("SendSpin playback paused")

    def resume(self) -> None:
        _LOGGER.info(
            "resume() called: stream_active=%s paused=%s owns=%s",
            self._stream_active,
            self._paused,
            self._sendspin_owns_playback,
        )
        if not (self._paused or self._sendspin_owns_playback):
            return
        self._paused = False
        self._update_player_volume()
        if self._client and self._client.connected:
            asyncio.get_event_loop().call_soon(lambda: asyncio.create_task(self._send_group_command(MediaCommand.PLAY)))
        _LOGGER.info("SendSpin playback resumed")

    @property
    def is_playing(self) -> bool:
        """Check if SendSpin is currently playing (not paused)."""
        return self._stream_active and not self._paused

    def stop(self) -> None:
        """Stop SendSpin playback (called when HA wants to play)."""
        if self._stream_active:
            _LOGGER.info("Stopping SendSpin playback (HA taking over)")
            self._stop_playback()
            self._stream_active = False
            self._paused = False

            # Report the current playback state using the enum supported by the installed aiosendspin version.
            if self._client and self._client.connected:
                asyncio.get_event_loop().call_soon(lambda: asyncio.create_task(self._send_group_command(MediaCommand.STOP)))

    async def start(self, server_url: str | None = None) -> None:
        if self._running:
            return

        await self._ensure_identity_and_store()
        self._client = self._create_client()

        self._client.add_audio_chunk_listener(self._on_audio_chunk)
        self._client.add_stream_start_listener(self._on_stream_start)
        self._client.add_stream_end_listener(self._on_stream_end)
        self._client.add_server_command_listener(self._on_server_command)
        self._client.add_controller_state_listener(self._on_controller_state)

        self._running = True

        if self.media_player:
            self._volume = int(self.media_player.volume * 100)
            self._muted = self.media_player.muted

        _LOGGER.info("Starting SendSpin bridge: %s", self.client_id)

        if server_url:
            # Explicit URL: dial out directly (manual/advanced override).
            asyncio.create_task(self._connection_loop(server_url))
        else:
            # Default: advertise via mDNS and accept inbound connections,
            # matching `sendspin daemon`'s zero-configuration behavior.
            asyncio.create_task(self._listener_loop())

    async def _listener_loop(self) -> None:
        """Advertise via mDNS and accept inbound connections from a Sendspin server."""
        self._listener = ClientListener(
            client_id=self.client_id,
            on_connection=self._handle_server_connection,
            path="/sendspin",
            host="0.0.0.0",
            advertise_mdns=True,
            client_name=self.client_name,
        )
        await self._listener.start()
        _LOGGER.info("SendSpin bridge advertising via mDNS as '%s'", self.client_id)

    async def _handle_server_connection(self, ws) -> None:
        """Handle an inbound connection from a Sendspin server (e.g. Music Assistant)."""
        if self._client is None:
            return
        attach_task = asyncio.create_task(self._client.attach_websocket(ws))
        try:
            for _ in range(100):  # ~10s handshake timeout
                if self._client.connected:
                    break
                if attach_task.done():
                    attach_task.result()
                    return
                await asyncio.sleep(0.1)
            else:
                _LOGGER.warning("SendSpin handshake did not complete in time")
                attach_task.cancel()
                return

            _LOGGER.info("SendSpin handshake complete with server")
            disconnect_event = asyncio.Event()
            unsubscribe = self._client.add_disconnect_listener(disconnect_event.set)
            await disconnect_event.wait()
            unsubscribe()
            _LOGGER.info("Disconnected from SendSpin server")
        finally:
            attach_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await attach_task

    async def disconnect(self) -> None:
        if not self._running:
            return
        _LOGGER.info("Stopping SendSpin bridge")
        self._running = False
        self._stop_playback()
        if self._listener is not None:
            await self._listener.stop()
            self._listener = None
        if self._client and self._client.connected:
            await self._client.disconnect()

    def _create_player(self, fmt: "AudioFormat") -> None:
        """Create AudioPlayer for the given format."""
        self._stop_player()

        if not self._client:
            return

        loop = asyncio.get_event_loop()

        self._player = AudioPlayer(
            loop=loop,
            compute_client_time=self._client.compute_play_time,
            compute_server_time=self._client.compute_server_time,
            device=self._audio_device,
        )

        # Set format to create stream
        self._player.set_format(fmt)

        # Set volume (respects current duck state)
        self._update_player_volume()

        self._current_format = fmt

        _LOGGER.info(
            "SendSpin audio player created: %d Hz, %d channels",
            fmt.pcm_format.sample_rate,
            fmt.pcm_format.channels,
        )

    def _stop_player(self) -> None:
        """Stop and clean up the AudioPlayer."""
        if self._player:
            try:
                # Close the stream immediately to release audio device
                if self._player._stream:
                    try:
                        self._player._stream.stop()
                        self._player._stream.close()
                    except Exception:
                        _LOGGER.debug("Error closing audio stream", exc_info=True)
                    self._player._stream = None

                # Clear buffer
                self._player.clear()

                # Schedule full cleanup in background
                loop = asyncio.get_event_loop()
                loop.create_task(self._player.stop())
            except Exception:
                _LOGGER.debug("Error stopping audio player", exc_info=True)
            self._player = None
        self._current_format = None

    def _stop_playback(self) -> None:
        """Stop playback."""
        self._stop_player()

    async def _connection_loop(self, url: str) -> None:
        """Connection loop with auto-reconnect."""
        if not self._client:
            return

        error_backoff = 1.0
        max_backoff = 300.0

        while self._running:
            try:
                await self._client.connect(url)
                error_backoff = 1.0

                # Wait for disconnect
                disconnect_event = asyncio.Event()
                unsubscribe = self._client.add_disconnect_listener(disconnect_event.set)
                await disconnect_event.wait()
                unsubscribe()

                _LOGGER.info("Disconnected from SendSpin server")

                if self._running:
                    _LOGGER.info("Reconnecting to %s", url)
            except Exception as e:
                _LOGGER.warning(
                    "SendSpin connection error (%s): %s, retrying in %.0fs",
                    type(e).__name__,
                    e,
                    error_backoff,
                )
                _LOGGER.debug("Full exception details:", exc_info=True)
                await asyncio.sleep(error_backoff)
                error_backoff = min(error_backoff * 2, max_backoff)

    def _on_stream_start(self, _message: StreamStartMessage) -> None:
        """Handle stream start from SendSpin server."""
        _LOGGER.info("SendSpin stream started")
        self._stream_active = True
        self._paused = False
        self._sendspin_owns_playback = True

        # Notify MediaPlayerEntity (it will handle pausing HA music and updating state)
        if self._on_sendspin_start:
            self._on_sendspin_start()

    def _on_stream_end(self, roles) -> None:
        """Handle stream end from SendSpin server."""
        _LOGGER.info("SendSpin stream ended")
        self._stream_active = False
        self._paused = False
        self._stop_playback()

        if self._paused:
            # Stream ended because of our pause. Keep _paused so resume() works
            # and keep the entity showing PAUSED.
            return

        # Stop playback
        self._stop_playback()

        # Update MediaPlayerEntity state to idle
        self._paused = False
        if self.media_player:
            self.media_player.server.send_messages([self.media_player._update_state(MediaPlayerState.IDLE)])

    def _on_server_command(self, payload: ServerCommandPayload) -> None:
        """Handle volume/mute commands from SendSpin server."""
        if payload.player is None or self._client is None:
            return

        player_cmd = payload.player

        if player_cmd.command == PlayerCommand.VOLUME and player_cmd.volume is not None:
            self._volume = max(0, min(100, player_cmd.volume))
            _LOGGER.info("SendSpin server set volume: %d", self._volume)
            self._update_player_volume()
            # Sync with MediaPlayerEntity
            if self.media_player:
                self.media_player.volume = self._volume / 100.0
                self.media_player.music_player.set_volume(self._volume)
                self.media_player.announce_player.set_volume(self._volume)
                self.media_player.server.send_messages([self.media_player._update_state(self.media_player.state)])

        elif player_cmd.command == PlayerCommand.MUTE and player_cmd.mute is not None:
            was_muted = self._muted
            self._muted = player_cmd.mute

            # Capture the volume to restore only at the moment mute first turns on,
            # so a later (or interleaved) VOLUME=0 push can't destroy the real value.
            if self._muted and not was_muted:
                self._pre_mute_volume = self._volume

            self._update_player_volume()

            if self.media_player:
                self.media_player.muted = self._muted
                if self._muted:
                    effective_volume = 0
                else:
                    effective_volume = self._pre_mute_volume
                    self._volume = self._pre_mute_volume  # restore the bridge's own record too

                self.media_player.volume = effective_volume / 100.0
                self.media_player.music_player.set_volume(effective_volume)
                self.media_player.announce_player.set_volume(effective_volume)
                self.media_player.server.send_messages([self.media_player._update_state(self.media_player.state)])
            _LOGGER.info("SendSpin server %s player", "muted" if player_cmd.mute else "unmuted")

        # Send state update back to server (same 0-100 scale, no conversion)
        asyncio.get_event_loop().call_soon(
            lambda v=self._volume: asyncio.create_task(
                self._client.send_player_state(
                    available=True,
                    volume=v,
                    muted=self._muted,
                )
            )
        )

    def _on_audio_chunk(
        self,
        server_timestamp_us: int,
        audio_data: bytes,
        fmt: "AudioFormat",
    ) -> None:
        """Handle incoming audio chunk from SendSpin."""
        if not self._stream_active or not self._client:
            return

        # Create player if needed or if format changed
        if self._player is None or self._current_format != fmt:
            self._create_player(fmt)

        # Submit chunk to player for time-synchronized playback
        if self._player:
            self._player.submit(server_timestamp_us, audio_data)

    @property
    def connected(self) -> bool:
        """Check if connected to SendSpin server."""
        return self._client is not None and self._client.connected
