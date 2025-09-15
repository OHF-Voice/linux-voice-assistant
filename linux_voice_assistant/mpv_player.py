# linux_voice_assistant/mpv_player.py
"""Media player using mpv in a subprocess.

- Supports ALSA and PulseAudio
- Duck/unduck
- Robust URL handling (str | list/tuple | bytes)
- Backwards-compatible API: play(url, done_callback=None, stop_first=False), pause, resume, stop, set_volume
- Pulse-friendly defaults (44100 Hz stereo) and short network timeout
- Watchdog is **disabled by default** (set watchdog_sec > 0 to enable)
- End-of-playback detection uses BOTH `eof-reached` and `idle-active` to finish immediately after short clips
- mpv log level can be controlled with env var LVA_MPV_MSG_LEVEL (e.g. "all=warn", "all=info")
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Sequence
from threading import Lock, Timer
from typing import Optional, Union

from mpv import MPV

_LOGGER = logging.getLogger(__name__)


class MpvMediaPlayer:
    def __init__(self, device: Optional[str] = None, watchdog_sec: float = 0.0) -> None:
        # Bridge mpv logs into Python logging
        self.player = MPV(video=False, terminal=False, log_handler=self._mpv_log)

        # Pulse-friendly defaults
        try:
            self.player['audio-samplerate'] = 44100
            self.player['audio-channels'] = 'stereo'
            self.player['keep-open'] = 'no'
            self.player['network-timeout'] = 7
            # Log level configurable via env; default to warn
            msg_level = os.environ.get("LVA_MPV_MSG_LEVEL", "all=warn")
            self.player['msg-level'] = msg_level
        except Exception:
            pass

        # Normalize/select backend and device
        ao = None
        norm = None

        if device:
            d = device.strip()
            if d.startswith("alsa/"):
                ao = "alsa"; norm = d
            elif d.startswith("pulse/"):
                ao = "pulse"; norm = d
            elif d == "default":
                try:
                    self.player["ao"] = "pulse"; norm = "pulse/default"
                except Exception:
                    try:
                        self.player["ao"] = "alsa"; norm = "alsa/default"
                    except Exception:
                        _LOGGER.warning("Neither Pulse nor ALSA available for 'default'")
            else:
                # Assume Pulse sink name if no prefix
                ao = "pulse"; norm = f"pulse/{d}"

        if ao:
            try:
                self.player["ao"] = ao
            except Exception:
                _LOGGER.warning("Requested ao=%s not available", ao)

        if norm:
            try:
                self.player["audio-device"] = norm
            except Exception:
                _LOGGER.warning("Failed to set audio-device=%s", norm)

        # State
        self.is_playing: bool = False
        self._done_callback: Optional[Callable[[], None]] = None
        self._done_callback_lock = Lock()
        self._pre_duck_volume: Optional[int] = None  # stores 0..100 when ducked
        self._watchdog: Optional[Timer] = None
        self._watchdog_sec: float = float(watchdog_sec)

        # Playback end detection (both EOF and idle)
        self.player.observe_property("eof-reached", self._on_eof)
        self.player.observe_property("idle-active", self._on_idle_active)

    # -------------------- public API --------------------

    def play(
        self,
        url: Union[str, Sequence[str]],
        done_callback: Optional[Callable[[], None]] = None,
        stop_first: bool = False,
    ) -> None:
        """Begin playback of a URL or local file. Replaces any existing playback.
        Accepts a single URL (str) or a sequence of URLs; if a sequence is given, the first item is played.
        """
        if stop_first:
            try:
                self.stop()
            except Exception:
                _LOGGER.debug("stop_first=True: stop() raised, continuing", exc_info=True)

        with self._done_callback_lock:
            self._done_callback = done_callback

        # Normalize url input
        if isinstance(url, (list, tuple)):
            if not url:
                _LOGGER.error("mpv play() received empty URL list")
                self._run_done_callback()
                return
            url = url[0]
        if isinstance(url, bytes):
            try:
                url = url.decode("utf-8")
            except Exception:
                url = url.decode(errors="ignore")
        if not isinstance(url, str):
            _LOGGER.error("mpv play() expected str URL, got %r", type(url))
            self._run_done_callback()
            return

        try:
            self.player["mute"] = "no"
        except Exception:
            pass

        self.is_playing = True
        try:
            self.player.play(url)
            self._arm_watchdog()
        except Exception:
            self.is_playing = False
            _LOGGER.exception("mpv failed to play %r", url)
            self._run_done_callback()

    def pause(self) -> None:
        try:
            self.player.pause = True
        except Exception:
            _LOGGER.exception("mpv pause() failed")

    def resume(self) -> None:
        try:
            self.player.pause = False
        except Exception:
            _LOGGER.exception("mpv resume() failed")

    def stop(self) -> None:
        """Stop playback immediately and clear playing state; run done callback."""
        try:
            try:
                self.player.command('stop')
            except Exception:
                self.player.command('loadfile', 'null://', 'replace')
        finally:
            self._cancel_watchdog()
            was_playing = self.is_playing
            self.is_playing = False
            if was_playing:
                self._run_done_callback()

    def set_volume(self, *args, **kwargs) -> None:
        """Set volume 0..100. Accepts positional or keyword 'volume' (backwards-compatible)."""
        vol_arg = args[0] if args else kwargs.get("volume")
        if vol_arg is None:
            _LOGGER.error("set_volume() requires an integer (0..100)")
            return

        try:
            vol = max(0, min(100, int(vol_arg)))
        except Exception:
            _LOGGER.exception("set_volume(): invalid value %r", vol_arg)
            return

        try:
            self.player.volume = vol
        except Exception:
            _LOGGER.exception("mpv set_volume(%s) failed", vol)

    def duck(self, target_percent: int = 20) -> None:
        if self._pre_duck_volume is not None:
            return  # already ducked

        try:
            current = int(round(float(self.player.volume)))
        except Exception:
            current = 100

        self._pre_duck_volume = max(0, min(100, current))

        try:
            self.player.volume = max(0, min(100, int(target_percent)))
        except Exception:
            _LOGGER.exception("duck(): failed to set duck volume")

    def unduck(self) -> None:
        if self._pre_duck_volume is None:
            return

        try:
            self.player.volume = self._pre_duck_volume
        except Exception:
            _LOGGER.exception("unduck(): failed to restore volume")
        finally:
            self._pre_duck_volume = None

    # -------------------- callbacks & watchdog --------------------

    def _on_eof(self, _name: str, reached: bool) -> None:
        if not reached:
            return
        self._cancel_watchdog()
        self.is_playing = False
        self._run_done_callback()

    def _on_idle_active(self, _name: str, active: bool) -> None:
        # When playback ends, mpv enters idle; treat that as completion.
        if not self.is_playing:
            return
        if bool(active):
            _LOGGER.debug("mpv became idle; treating as end-of-playback")
            self._cancel_watchdog()
            self.is_playing = False
            self._run_done_callback()

    def _arm_watchdog(self) -> None:
        self._cancel_watchdog()
        if self._watchdog_sec <= 0:
            return
        self._watchdog = Timer(self._watchdog_sec, self._watchdog_trip)
        self._watchdog.daemon = True
        self._watchdog.start()

    def _cancel_watchdog(self) -> None:
        if self._watchdog is not None:
            try:
                self._watchdog.cancel()
            except Exception:
                pass
            self._watchdog = None

    def _watchdog_trip(self) -> None:
        if self.is_playing:
            _LOGGER.warning("mpv watchdog fired after %.1fs; stopping playback", self._watchdog_sec)
            try:
                self.player.command('stop')
            except Exception:
                pass
            self.is_playing = False
            self._run_done_callback()

    def _run_done_callback(self) -> None:
        cb: Optional[Callable[[], None]] = None
        with self._done_callback_lock:
            cb = self._done_callback
            self._done_callback = None

        if cb is not None:
            try:
                cb()
            except Exception:
                _LOGGER.exception("Unexpected error running done callback")

    # -------------------- mpv log bridge --------------------
    def _mpv_log(self, level: str, prefix: str, text: str) -> None:
        msg = f"mpv[{level}] {prefix}: {text}".rstrip()
        if level in ("fatal", "error"):
            _LOGGER.error(msg)
        elif level in ("warn", "warning"):
            _LOGGER.warning(msg)
        elif level in ("info",):
            _LOGGER.info(msg)
        else:
            _LOGGER.debug(msg)

