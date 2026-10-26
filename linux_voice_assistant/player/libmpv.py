import logging
import threading
from typing import Callable, Optional

import mpv

from linux_voice_assistant.player.base import AudioPlayer
from linux_voice_assistant.player.state import PlayerState

_AUDIO_BUFFER_SECS = 0.8
# mpv reports eof once the last samples are handed to the audio output, so the
# end of the file is audible about this long after the event arrives.
_EOF_DRAIN_DELAY_SECS = _AUDIO_BUFFER_SECS + 0.1


class LibMpvPlayer(AudioPlayer):
    """
    AudioPlayer implementation for Linux Voice Assistant using libmpv.

    Responsibilities:
    - mpv lifecycle and playback control
    - thread-safe state management
    - volume handling with ducking support
    """

    def __init__(self, device: Optional[str] = None) -> None:
        self._log = logging.getLogger(self.__class__.__name__)
        self._state: PlayerState = PlayerState.IDLE
        self._state_lock = threading.Lock()
        self._last_error: Optional[str] = None

        # Volume handling
        self._user_volume: float = 100.0  # 0.0 – 100.0
        self._duck_factor: float = 1.0  # 0.0 – 1.0

        # mpv setup
        self._mpv = mpv.MPV(
            audio_display=False,
            video=False,
            log_handler=self._on_mpv_log,
            loglevel="error",
            cache="yes",
            demuxer_max_bytes="16MiB",
            cache_secs="20",
        )

        if device:
            self._mpv["audio-device"] = device

        # Pre-buffer audio before the sink starts clocking samples out.
        # The default (0.2 s) is too tight for short notification sounds on
        # PulseAudio/PipeWire: the sink stream takes a few ms to initialise
        # and the very first samples are dropped before it is ready, making
        # short files (<1 s) appear to start mid-way through.
        # 0.8 s gives the output pipeline enough headroom without adding any
        # noticeable latency for a user-facing notification sound.
        self._mpv["audio-buffer"] = _AUDIO_BUFFER_SECS

        # Feed silence instead of stopping the audio output on pause and seeks.
        # mpv only does this while a file is loaded: once playback ends the
        # audio device is closed, so this does NOT keep the PulseAudio/PipeWire
        # stream open between files.
        self._mpv["audio-stream-silence"] = True

        # Callback Handling
        self._done_callback: Optional[Callable[[], None]] = None
        self._eof_timer: Optional[threading.Timer] = None
        self._mpv.event_callback("end-file")(self._on_end_file)
        self._mpv.event_callback("start-file")(self._on_start_file)

    # -------- Playback control --------

    def play(
        self,
        url: str,
        done_callback: Optional[Callable[[], None]] = None,
        stop_first: bool = True,
    ) -> None:
        """
        Start playback of a media URL.

        Args:
            url: Media URL or local file path.
            done_callback: Optional callback invoked when playback finishes.
            stop_first: If True, start playback in paused state.
        """
        with self._state_lock:
            self._log.debug("play: current_state=%s", self._state)
            self._cancel_eof_timer()
            self._done_callback = done_callback
            self._last_error = None
            self._set_state(PlayerState.LOADING)
        self._mpv.pause = stop_first
        self._mpv.play(url)

    def pause(self) -> None:
        """Pause playback."""
        with self._state_lock:
            self._mpv.pause = True
            self._set_state(PlayerState.PAUSED)

    def resume(self) -> None:
        """Resume playback if paused."""
        self._log.debug("resume() called")
        with self._state_lock:
            self._mpv.pause = False
            self._set_state(PlayerState.PLAYING)

    def stop(self, for_replacement: bool = False) -> None:
        """
        Stop playback.

        If called for track replacement, clears the callback to prevent
        it from being invoked during the transition.
        """
        self._log.debug("stop() called")
        with self._state_lock:
            self._cancel_eof_timer()
            if for_replacement:
                # Clear callback to prevent invocation during track transition
                self._done_callback = None
            else:
                # mpv reports a stop as a non-eof end-file, which _on_end_file ignores
                self._set_state(PlayerState.IDLE)
            self._mpv.stop()

    def state(self) -> PlayerState:
        """Return the current player state."""
        with self._state_lock:
            return self._state

    # -------- Volume / Ducking --------

    def set_volume(self, volume: float) -> None:
        """
        Set user volume.

        Args:
            volume: Volume level (0.0–100.0).
        """
        self._log.debug("set_volume() called")
        with self._state_lock:
            self._user_volume = max(0.0, min(100.0, float(volume)))
            self._apply_volume()

    def duck(self, factor: float = 0.5) -> None:
        """
        Reduce volume temporarily by a ducking factor.

        Args:
            factor: Ducking factor (0.0–1.0).
        """
        self._log.debug("duck() called")
        with self._state_lock:
            self._duck_factor = max(0.0, min(1.0, float(factor)))
            self._apply_volume()

    def unduck(self) -> None:
        """Restore volume to the user-defined level."""
        self._log.debug("unduck() called")
        with self._state_lock:
            self._duck_factor = 1.0
            self._apply_volume()

    # -------- Internal helpers --------

    def _apply_volume(self) -> None:
        """Apply effective volume (user volume × duck factor) to mpv."""
        self._log.debug("_apply_volume() called")
        effective = self._user_volume * self._duck_factor
        self._mpv.volume = max(0.0, min(100.0, effective))

    def _cancel_eof_timer(self) -> None:
        """Cancel a pending end-of-playback timer. Must be called with the state lock held."""
        if self._eof_timer is not None:
            self._eof_timer.cancel()
            self._eof_timer = None

    def _on_end_file(self, event) -> None:
        with self._state_lock:
            # mpv events: event.data is a MpvEventEndFile object with a 'reason' attribute
            # The reason is an integer constant (see mpv.END_FILE_REASON_*)
            end_file_data = event.data
            reason = getattr(end_file_data, "reason", -1) if end_file_data else -1

            # mpv END_FILE_REASON constants:
            # 0 = eof (end of file), 1 = stop, 2 = abort, 3 = quit, 4 = error
            is_eof = reason == 0
            is_error = reason == 4

            self._log.debug(
                "_on_end_file: reason=%s (is_eof=%s), state=%s, has_callback=%s",
                reason,
                is_eof,
                self._state,
                self._done_callback is not None,
            )

            # Only process "eof" (reason=0) and "error" (reason=4) events as track completion.
            # Other reasons are from track changes or stops, which stop() handles itself.
            if not (is_eof or is_error):
                self._log.debug("_on_end_file: ignoring non-completion event (reason=%s)", reason)
                return

            # A failed or empty file never reached the audio output, so there is nothing to drain.
            # Without the callback the pipeline would stay in "responding" and block the wake word.
            if is_error:
                self._log.error("Playback failed: %s", self._last_error or "unknown mpv error")
                self._log.warning("_on_end_file: playback ended with error; invoking done_callback")

            self._cancel_eof_timer()
            timer = threading.Timer(0 if is_error else _EOF_DRAIN_DELAY_SECS, self._finish_playback)
            timer.daemon = True
            self._eof_timer = timer
            timer.start()

    def _finish_playback(self) -> None:
        """Mark playback as finished and invoke the done callback once the audio has been played out."""
        with self._state_lock:
            if self._eof_timer is not threading.current_thread():
                return
            self._eof_timer = None
            self._set_state(PlayerState.IDLE)
            callback = self._done_callback
            self._done_callback = None

        if callback is not None:
            self._log.debug("_finish_playback: invoking callback")
            try:
                callback()
            except RuntimeError:
                # Callback errors must never break the player
                pass

    def _on_start_file(self, event) -> None:
        """Called when mpv starts playing a file."""
        self._log.debug("_on_start_file() called")
        with self._state_lock:
            self._log.debug("_on_start_file: state=%s", self._state)
            self._set_state(PlayerState.PLAYING)

    def _on_mpv_log(self, level: str, prefix: str, text: str) -> None:
        """
        Handle mpv log messages.

        Error and fatal messages transition the player into ERROR state.
        """
        if level in ("error", "fatal"):
            with self._state_lock:
                self._last_error = f"[{prefix}] {text.strip()}"
                self._set_state(PlayerState.ERROR)

    def _set_state(self, new_state: PlayerState) -> None:
        """Update internal player state."""
        self._state = new_state
