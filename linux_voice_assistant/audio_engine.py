"""Audio processing engine."""

import asyncio
import logging
import threading
import time
import warnings
from typing import Dict, List, Optional, Union

import numpy as np
import soundcard as sc

from pymicro_wakeword import MicroWakeWord, MicroWakeWordFeatures
from pyopen_wakeword import OpenWakeWord, OpenWakeWordFeatures

from .models import ServerState
from .utilities import clamp_0_1

_LOGGER = logging.getLogger(__name__)

# Suppress the "log(0)" warning from pymicro_wakeword
warnings.filterwarnings("ignore", category=RuntimeWarning, module="pymicro_wakeword")


class AudioEngine:
    """
    Audio processing engine for wake word detection and voice streaming.

    Runs a background thread that continuously processes audio from the microphone,
    detecting wake words (both MicroWakeWord and OpenWakeWord), stop words, and
    streaming audio to the satellite for voice assistant processing.

    Thread-safe with automatic buffer management and refractory period handling.
    """

    def __init__(self, state: ServerState, mic, block_size: int, oww_threshold: float = 0.5):
        """
        Initialize the audio engine.

        Args:
            state: Shared server state containing wake words and configuration
            mic: Soundcard microphone device for audio input
            block_size: Number of audio samples to process per block (typically 512 or 1024)
            oww_threshold: OpenWakeWord activation threshold (0.0-1.0, default 0.5)
        """
        self.state = state
        self.mic = mic
        self.block_size = block_size
        self._thread: Optional[threading.Thread] = None

        # Configurable OpenWakeWord activation threshold (default matches prior behavior)
        self.oww_threshold = clamp_0_1("wake_word.openwakeword_threshold", oww_threshold, default=0.5)

    def start(self):
        """Starts the audio processing thread."""
        self._thread = threading.Thread(
            target=self._process_audio,
            daemon=True,
            name="AudioEngineThread",
        )
        self._thread.start()

    def stop(self):
        """Stops the audio processing thread."""
        self.state.shutdown = True
        # Ensure thread wakes up if waiting
        self.state.mic_muted_event.set()
        if self._thread:
            self._thread.join()

    def _process_audio(self):
        """Main audio processing loop."""
        _LOGGER.debug("Audio engine started. OpenWakeWord default threshold=%.2f", self.oww_threshold)

        micro_features = MicroWakeWordFeatures()
        oww_features: Optional[OpenWakeWordFeatures] = None

        wake_words: List[Union[MicroWakeWord, OpenWakeWord]] = []
        micro_inputs: List[np.ndarray] = []
        oww_inputs: List[np.ndarray] = []
        has_oww = False

        # Track last activation time per wake word (by id or phrase)
        last_active_by_id: Dict[str, float] = {}

        try:
            while True:
                # --- Handle Mute State ---
                if not self.state.mic_muted_event.is_set():
                    _LOGGER.debug("Audio thread muted. Waiting...")
                    self.state.mic_muted_event.wait()

                    if self.state.shutdown:
                        return

                    # Unmuted: Clear buffers and prepare
                    _LOGGER.debug("Audio thread unmuted. Flushing...")
                    micro_features.reset()
                    if oww_features is not None:
                        oww_features.reset()

                    # Allow immediate activation (don't enforce refractory here)
                    last_active_by_id.clear()

                    # Flush hardware buffer
                    try:
                        with self.mic.recorder(
                            samplerate=16000,
                            channels=1,
                            blocksize=self.block_size,
                        ) as mic_in:
                            mic_in.flush()
                    except Exception as e:
                        _LOGGER.warning("Error flushing mic: %s", e)

                if self.state.shutdown:
                    return

                # --- Main Recording Loop ---
                _LOGGER.debug("Opening audio input device: %s", self.mic.name)
                with self.mic.recorder(
                    samplerate=16000, channels=1, blocksize=self.block_size
                ) as mic_in:
                    while self.state.mic_muted_event.is_set():
                        if self.state.shutdown:
                            return

                        audio_chunk_array = mic_in.record(self.block_size).reshape(-1)
                        audio_chunk = (
                            (np.clip(audio_chunk_array, -1.0, 1.0) * 32767.0)
                            .astype("<i2")
                            .tobytes()
                        )

                        if self.state.satellite is None:
                            time.sleep(0.01)
                            continue

                        # CRITICAL FIX: Protect wake word reload with lock
                        with self.state.wake_words_lock:
                            # Update active wake words if changed
                            if (not wake_words) or (self.state.wake_words_changed):
                                self.state.wake_words_changed = False
                                wake_words = [
                                    ww
                                    for ww in self.state.wake_words.values()
                                    if ww.id in self.state.active_wake_words
                                ]
                                has_oww = any(
                                    isinstance(ww, OpenWakeWord) for ww in wake_words
                                )

                                if has_oww and (oww_features is None):
                                    _LOGGER.debug(
                                        "Initializing OpenWakeWord features..."
                                    )
                                    oww_features = OpenWakeWordFeatures.from_builtin()

                                _LOGGER.debug(
                                    "Rebuilt wake_words list: %s (has_oww=%s, active_ids=%s)",
                                    [
                                        getattr(ww, "wake_word", getattr(ww, "id", "?"))
                                        for ww in wake_words
                                    ],
                                    has_oww,
                                    self.state.active_wake_words,
                                )

                        try:
                            # Feature extraction
                            micro_inputs.clear()
                            micro_inputs.extend(
                                micro_features.process_streaming(audio_chunk)
                            )

                            if has_oww and oww_features:
                                oww_inputs.clear()
                                oww_inputs.extend(
                                    oww_features.process_streaming(audio_chunk)
                                )

                            # Wake word detection
                            for wake_word in wake_words:
                                activated = False
                                if isinstance(wake_word, MicroWakeWord):
                                    if any(
                                        wake_word.process_streaming(mi)
                                        for mi in micro_inputs
                                    ):
                                        activated = True
                                elif isinstance(wake_word, OpenWakeWord):
                                    threshold = getattr(wake_word, "threshold", self.oww_threshold)
                                    if any(
                                        p > threshold
                                        for oi in oww_inputs
                                        for p in wake_word.process_streaming(oi)
                                    ):
                                        activated = True

                                if activated:
                                    wake_phrase = getattr(
                                        wake_word, "wake_word", "wake word"
                                    )
                                    wake_id_attr = getattr(wake_word, "id", None)
                                    # Use id if available, otherwise phrase string as key
                                    id_key = wake_id_attr or wake_phrase

                                    _LOGGER.debug(
                                        "Wake word ACTIVATED: %s (id=%s, threshold=%.2f)",
                                        wake_phrase,
                                        wake_id_attr,
                                        getattr(wake_word, "threshold", self.oww_threshold)
                                        if isinstance(wake_word, OpenWakeWord)
                                        else -1.0,
                                    )

                                    now = time.monotonic()
                                    last_active = last_active_by_id.get(id_key)

                                    # If refractory_seconds <= 0, disable gating
                                    refractory = max(self.state.refractory_seconds, 0.0)

                                    if (last_active is None) or (
                                        (now - last_active) > refractory
                                    ):
                                        _LOGGER.debug(
                                            "Wake word ACCEPTED (phrase=%s); "
                                            "triggering wakeup (Δt=%.2f, refractory=%.2f)",
                                            wake_phrase,
                                            0.0
                                            if last_active is None
                                            else now - last_active,
                                            refractory,
                                        )
                                        last_active_by_id[id_key] = now
                                        self.state.loop.call_soon_threadsafe(
                                            self.state.satellite.wakeup, wake_word
                                        )
                                    else:
                                        _LOGGER.debug(
                                            "Wake word REJECTED by refractory period "
                                            "(phrase=%s, Δt=%.2f < %.2f)",
                                            wake_phrase,
                                            now - last_active,
                                            refractory,
                                        )

                            # Stop word detection
                            stopped = False
                            for micro_input in micro_inputs:
                                if self.state.stop_word.process_streaming(micro_input):
                                    stopped = True

                            if stopped:
                                _LOGGER.debug(
                                    "Stop wake word ACTIVATED (id=%s, active_wake_words=%s)",
                                    getattr(self.state.stop_word, "id", None),
                                    self.state.active_wake_words,
                                )

                            if (
                                stopped
                                and self.state.stop_word.id
                                in self.state.active_wake_words
                            ):
                                _LOGGER.debug(
                                    "Stop wake word ACCEPTED; calling satellite.stop()"
                                )
                                self.state.loop.call_soon_threadsafe(
                                    self.state.satellite.stop
                                )
                            elif stopped:
                                _LOGGER.debug(
                                    "Stop wake word TRIGGERED but IGNORED because "
                                    "id=%s not in active_wake_words",
                                    getattr(self.state.stop_word, "id", None),
                                )

                            # Pass audio to satellite (for voice streaming)
                            self.state.loop.call_soon_threadsafe(
                                self.state.satellite.handle_audio, audio_chunk
                            )

                        except (KeyboardInterrupt, SystemExit):
                            raise
                        except Exception as e:
                            _LOGGER.error("Unexpected error handling audio: %s", e, exc_info=True)
                            # Continue processing - this is recoverable

        except (KeyboardInterrupt, SystemExit):
            _LOGGER.info("Audio engine shutting down due to interrupt")
            raise
        except OSError as e:
            _LOGGER.critical("Audio device error (hardware failure): %s", e, exc_info=True)
            self.state.loop.call_soon_threadsafe(self.state.loop.stop)
        except Exception as e:
            _LOGGER.critical("Fatal error in audio engine: %s", e, exc_info=True)
            self.state.loop.call_soon_threadsafe(self.state.loop.stop)
