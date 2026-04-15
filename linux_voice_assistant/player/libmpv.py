import logging
import threading
from typing import Callable, Optional

import mpv

from linux_voice_assistant.player.base import AudioPlayer
from linux_voice_assistant.player.state import PlayerState


class LibMpvPlayer(AudioPlayer):
    """
    AudioPlayer implementation for Linux Voice Assistant using libmpv.

    Responsibilities:
    - mpv lifecycle and playback control
    - thread-safe state management
    - volume handling with ducking support

    NOTE on end-file events (#179):
    When mpv stops one file to start another it fires an end-file event with
    reason=1 (stop), NOT reason=0 (eof).  We must NEVER invoke the
    done-callback for non-EOF reasons because that would fire "I finished
    naturally" at the wrong time.  However we DO still set state to IDLE so
    that callers that check state() immediately after stop() see a consistent
    value (fixes the double-stop race in MpvMediaPlayer.play).
    """

    def __init__(self, device: Optional[str] = None) -> None:
        self._log = logging.getLogger(self.__class__.__name__)
        self._state: PlayerState = PlayerState.IDLE
        self._state_lock = threading.Lock()

        # Volume handling
        self._user_volume: float = 100.0  # 0.0 – 100.0
        self._duck_factor: float = 1.0  # 0.0 – 1.0

        # mpv setup
        self._mpv = mpv.MPV(
            audio_display=False,
            log_handler=self._on_mpv_log,
            loglevel="error",
        )

        if device:
            self._mpv["audio-device"] = device

        # Pre-buffer audio before the sink starts clocking samples out.
        # The default (0.2 s) is too tight for short notification sounds on
        # PulseAudio/PipeWire: the sink stream takes a few ms to initialise
        # and the very first samples are dropped before it is ready, making
        # short files (<1 s) appear to start mid-way through.
        # 0.5 s gives the output pipeline enough headroom without adding any
        # noticeable latency for a user-facing notification sound.
        self._mpv["audio-buffer"] = 0.5

        # Keep the PulseAudio/PipeWire stream open between files by outputting
        # silence when idle.  This eliminates the per-play sink re-initialisation
        # penalty entirely, so back-to-back short sounds (wakeup → TTS, mute →
        # unmute) never lose their first samples regardless of system load.
        self._mpv["audio-stream-silence"] = True

        # Callback Handling
        self._done_callback: Optional[Callable[[], None]] = None
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
            done_callback: Optional callback invoked when playback finishes naturally.
            stop_first: If True, start playback in paused state.
        """
        with self._state_lock:
            self._log.debug("play: current_state=%s", self._state)
            self._done_callback = done_callback
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
        with self._state_lock:
            self._mpv.pause = False
            self._set_state(PlayerState.PLAYING)

    def stop(self, for_replacement: bool = False) -> None:
        """
        Stop playback.

        Sets state to IDLE immediately so callers that check state() right
        after stop() never see a stale PLAYING/LOADING value (which would
        cause MpvMediaPlayer.play() to issue a redundant second stop).

        If for_replacement=True the done-callback is cleared so it is never
        invoked during a track transition.  Either way the callback is NOT
        invoked here — only natural EOF (reason=0 in _on_end_file) triggers it.

        NOTE: _mpv.stop() is called OUTSIDE the lock so the mpv event thread
        can acquire _state_lock while processing the resulting end-file event
        without contention.
        """
        with self._state_lock:
            if for_replacement:
                self._done_callback = None
            # Mark IDLE immediately; _on_end_file will see reason=1 and return
            # early without touching state again (see guard below).
            self._set_state(PlayerState.IDLE)

        # Issue the stop command after releasing the lock.
        self._mpv.stop()

    def state(self) -> PlayerState:
        """Return the current player state."""
        with self._state_lock:
            return self._state

    # -------- Volume / Ducking --------

    def set_volume(self, volume: float) -> None:
        """Set user volume (0.0–100.0)."""
        with self._state_lock:
            self._user_volume = max(0.0, min(100.0, float(volume)))
            self._apply_volume()

    def duck(self, factor: float = 0.5) -> None:
        """Reduce volume temporarily by a ducking factor (0.0–1.0)."""
        with self._state_lock:
            self._duck_factor = max(0.0, min(1.0, float(factor)))
            self._apply_volume()

    def unduck(self) -> None:
        """Restore volume to the user-defined level."""
        with self._state_lock:
            self._duck_factor = 1.0
            self._apply_volume()

    # -------- Internal helpers --------

    def _apply_volume(self) -> None:
        """Apply effective volume (user volume × duck factor) to mpv."""
        effective = self._user_volume * self._duck_factor
        self._mpv.volume = max(0.0, min(100.0, effective))

    def _on_end_file(self, event) -> None:
        """
        Called by mpv whenever a file ends for any reason.

        reason=0  EOF  — natural end; invoke done-callback.
        reason=1  stop — explicit stop() or track replacement; do NOT invoke
                         callback, but DO clear it so it cannot fire later.
        reason=2+ other — treat same as stop: clear without invoking.

        State is always set to IDLE here so that any caller which re-reads
        state() after a stop gets a consistent value.  The only exception is
        if we already set IDLE in stop() before the event arrived; that is
        harmless (idempotent).
        """
        callback: Optional[Callable[[], None]] = None

        with self._state_lock:
            end_file_data = event.data
            reason = getattr(end_file_data, "reason", -1) if end_file_data else -1
            is_eof = reason == 0

            self._log.debug(
                "_on_end_file: reason=%s (is_eof=%s), state=%s, has_callback=%s",
                reason,
                is_eof,
                self._state,
                self._done_callback is not None,
            )

            # Always land in IDLE regardless of reason.
            self._set_state(PlayerState.IDLE)

            if not is_eof:
                # Non-EOF (stop/abort/error/replacement): clear callback without
                # invoking it.  This is the #179 fix — never fire the "finished
                # naturally" signal on an explicit stop.
                self._log.debug("_on_end_file: non-eof (reason=%s) — clearing callback without invoking", reason)
                self._done_callback = None
                return

            callback = self._done_callback
            self._done_callback = None

        if callback is not None:
            self._log.debug("_on_end_file: invoking done-callback (natural EOF)")
            try:
                callback()
            except RuntimeError:
                # Callback errors must never crash the player
                pass

    def _on_start_file(self, event) -> None:
        """Called when mpv starts playing a file."""
        with self._state_lock:
            self._log.debug("_on_start_file: state=%s", self._state)
            self._set_state(PlayerState.PLAYING)

    def _on_mpv_log(self, level: str, prefix: str, text: str) -> None:
        """Handle mpv log messages; error/fatal transition to ERROR state."""
        if level in ("error", "fatal"):
            with self._state_lock:
                self._set_state(PlayerState.ERROR)

    def _set_state(self, new_state: PlayerState) -> None:
        """Update internal player state (must be called with _state_lock held)."""
        self._state = new_state
