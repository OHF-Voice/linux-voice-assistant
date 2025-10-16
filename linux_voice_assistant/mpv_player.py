"""
Media player using mpv in a subprocess.

This hybrid version provides both explicit user control and smart auto-detection for the audio backend.
"""
from __future__ import annotations

import logging
import os
from threading import Lock, Timer
from typing import Callable, List, Optional, Sequence, Tuple, Union

from mpv import MPV

_LOGGER = logging.getLogger(__name__)

# -----------------------------------------------------------------------------

def _set_player_opt(player: MPV, key: str, value) -> bool:
    """Safely set an mpv option, logging any errors."""
    try:
        player[key] = value
        return True
    except Exception:
        _LOGGER.warning("Failed to set mpv option %r=%r", key, value, exc_info=True)
        return False

def _select_backend(player: MPV, device: Optional[str]) -> None:
    """
    Selects the best available audio output backend for mpv.

    It follows a hybrid approach:
    1.  **Explicit Control:** If a fully qualified device string (e.g., 'pulse/sink-name')
        is provided, it is used directly without modification.
    2.  **Smart Auto-Detection:** If a simple name ('default') or no device is given,
        it probes for the best available backend in a prioritized order.
    """
    candidates: List[Tuple[str, str]] = []
    preferred_ao_order = ["pipewire", "pulse", "alsa"]

    # 1. Check for an explicit, fully qualified device string from the user.
    if device:
        for prefix in preferred_ao_order:
            if device.startswith(f"{prefix}/"):
                # User provided a full path, e.g., "pulse/name-of-sink". Trust them.
                candidates.append((prefix, device))
                break # Stop searching, we have our explicit candidate.

    # 2. If no explicit path was found, build a list of candidates for auto-detection.
    if not candidates:
        device_name = device if device else "default"
        for ao in preferred_ao_order:
            candidates.append((ao, f"{ao}/{device_name}"))

    # Try to set the audio output from the generated candidates
    for ao, audio_device in candidates:
        try:
            player["ao"] = ao
            player["audio-device"] = audio_device
            _LOGGER.debug("mpv backend selected: ao=%s, audio-device=%s", ao, audio_device)
            return
        except Exception:
            continue
            
    _LOGGER.warning("No suitable audio output backend could be found.")


class MpvMediaPlayer:
    """A media player class that wraps the python-mpv library."""
    def __init__(self, device: Optional[str] = None, initial_volume: float = 1.0) -> None:
        self.player = MPV(
            video=False,
            terminal=False,
            log_handler=self._mpv_log,
            audio_samplerate=44100,
            audio_channels="stereo",
            keep_open="no",
            network_timeout=7,
            msg_level=os.environ.get("LVA_MPV_MSG_LEVEL", "all=warn"),
        )

        _select_backend(self.player, device)
        self.set_volume(int(initial_volume * 100))

        self.is_playing: bool = False
        self._playlist: List[str] = []
        self._done_callback: Optional[Callable[[], None]] = None
        self._done_callback_lock = Lock()
        self._pre_duck_volume: Optional[int] = None

        self.player.observe_property("idle-active", self._on_idle_active)

    def play(
        self,
        url: Union[str, Sequence[str], bytes],
        done_callback: Optional[Callable[[], None]] = None,
    ) -> None:
        """Plays a URL or a sequence of URLs."""
        self.stop() 

        with self._done_callback_lock:
            self._done_callback = done_callback

        playlist: List[str] = []
        if isinstance(url, (list, tuple)):
            playlist = list(url)
        elif isinstance(url, bytes):
            playlist = [url.decode(errors="ignore")]
        elif isinstance(url, str):
            playlist = [url]
        else:
            _LOGGER.error("play() expected str, bytes, or sequence, got %r", type(url))
            self._run_done_callback()
            return
        
        if not playlist:
            self._run_done_callback()
            return
            
        self.player.playlist_clear()
        for item in playlist:
            self.player.playlist_append(item)

        self.is_playing = True
        self.player.playlist_pos = 0
        
    def pause(self) -> None:
        """Pauses playback."""
        self.player.pause = True

    def resume(self) -> None:
        """Resumes playback."""
        self.player.pause = False

    def stop(self) -> None:
        """Stops playback and clears the playlist."""
        if self.is_playing:
            self.is_playing = False
            self.player.playlist_clear()
            self.player.command("stop")
            self._run_done_callback()
            
    def set_volume(self, volume: int) -> None:
        """Sets the player volume from 0 to 100."""
        try:
            self.player.volume = max(0, min(100, volume))
        except Exception:
            _LOGGER.exception("set_volume() failed")

    def duck(self, target_percent: int = 20) -> None:
        """Lowers the volume for an announcement."""
        if self._pre_duck_volume is not None:
            return
        try:
            self._pre_duck_volume = int(self.player.volume)
            self.set_volume(target_percent)
        except Exception:
            _LOGGER.exception("duck() failed")

    def unduck(self) -> None:
        """Restores the volume after an announcement."""
        if self._pre_duck_volume is None:
            return
        try:
            self.set_volume(self._pre_duck_volume)
        finally:
            self._pre_duck_volume = None

    def _on_idle_active(self, _name: str, active: bool) -> None:
        """Callback triggered when mpv enters or leaves the idle state."""
        if active and self.is_playing:
            _LOGGER.debug("mpv became idle; treating as end-of-playback")
            self.is_playing = False
            self._run_done_callback()

    def _run_done_callback(self) -> None:
        """Safely runs the done_callback if it exists."""
        with self._done_callback_lock:
            cb = self._done_callback
            self._done_callback = None
        if cb:
            try:
                cb()
            except Exception:
                _LOGGER.exception("Error in done_callback")

    def _mpv_log(self, level: str, prefix: str, text: str) -> None:
        """Routes mpv's internal logs to our logger."""
        msg = f"mpv[{prefix}]: {text}".rstrip()
        if level == "error":
            _LOGGER.error(msg)
        elif level == "warn":
            _LOGGER.warning(msg)
        elif level == "info":
            _LOGGER.info(msg)
        else:
            _LOGGER.debug(msg)"""
Media player using mpv in a subprocess.
Includes logic to detect the audio server being used (Pirewire, PulseAudio, Alsa).
This refactored version simplifies the audio backend selection logic,
improves readability with docstrings, and standardizes method signatures.
"""
from __future__ import annotations

import logging
import os
from threading import Lock, Timer
from typing import Callable, Iterable, List, Optional, Sequence, Tuple, Union

from mpv import MPV

_LOGGER = logging.getLogger(__name__)

# -----------------------------------------------------------------------------

def _set_player_opt(player: MPV, key: str, value) -> bool:
    """Safely set an mpv option, logging any errors."""
    try:
        player[key] = value
        return True
    except Exception:
        _LOGGER.warning("Failed to set mpv option %r=%r", key, value, exc_info=True)
        return False

def _select_backend(player: MPV, device: Optional[str]) -> None:
    """
    Selects the best available audio output backend for mpv.
    
    The selection follows a clear priority:
    1. A fully qualified device string (e.g., 'alsa/plughw:...') is used directly.
    2. A simple device name tries PipeWire, then PulseAudio, then ALSA.
    3. No device specified (or 'default') tries the default for PipeWire, PulseAudio, and ALSA in order.
    """
    candidates: List[Tuple[str, str]] = []
    
    # Define the preferred order of audio outputs
    preferred_ao_order = ["pipewire", "pulse", "alsa"]

    if device:
        # If a full device path is given (e.g., "alsa/plughw:...")
        for prefix in preferred_ao_order:
            if device.startswith(f"{prefix}/"):
                candidates.append((prefix, device))
                break
        
        # If it's a simple name or "default"
        if not candidates:
            for ao in preferred_ao_order:
                candidates.append((ao, f"{ao}/{device}"))
    else:
        # If no device is specified, try the default for each backend
        for ao in preferred_ao_order:
            candidates.append((ao, f"{ao}/default"))

    # Try to set the audio output from the generated candidates
    for ao, audio_device in candidates:
        try:
            player["ao"] = ao
            player["audio-device"] = audio_device
            _LOGGER.debug("mpv backend selected: ao=%s, audio-device=%s", ao, audio_device)
            return
        except Exception:
            continue
            
    _LOGGER.warning("No suitable audio output backend could be found.")


class MpvMediaPlayer:
    """A media player class that wraps the python-mpv library."""
    def __init__(self, device: Optional[str] = None, initial_volume: float = 1.0) -> None:
        self.player = MPV(
            video=False,
            terminal=False,
            log_handler=self._mpv_log,
            # Set options directly in the constructor
            audio_samplerate=44100,
            audio_channels="stereo",
            keep_open="no",
            network_timeout=7,
            msg_level=os.environ.get("LVA_MPV_MSG_LEVEL", "all=warn"),
        )

        _select_backend(self.player, device)
        self.set_volume(int(initial_volume * 100))

        self.is_playing: bool = False
        self._playlist: List[str] = []
        self._done_callback: Optional[Callable[[], None]] = None
        self._done_callback_lock = Lock()
        self._pre_duck_volume: Optional[int] = None

        self.player.observe_property("idle-active", self._on_idle_active)

    def play(
        self,
        url: Union[str, Sequence[str], bytes],
        done_callback: Optional[Callable[[], None]] = None,
    ) -> None:
        """Plays a URL or a sequence of URLs."""
        self.stop() # Ensure player is in a clean state

        with self._done_callback_lock:
            self._done_callback = done_callback

        playlist: List[str] = []
        if isinstance(url, (list, tuple)):
            playlist = list(url)
        elif isinstance(url, bytes):
            playlist = [url.decode(errors="ignore")]
        elif isinstance(url, str):
            playlist = [url]
        else:
            _LOGGER.error("play() expected str, bytes, or sequence, got %r", type(url))
            self._run_done_callback()
            return
        
        if not playlist:
            self._run_done_callback()
            return
            
        # Load the full playlist into mpv
        self.player.playlist_clear()
        for item in playlist:
            self.player.playlist_append(item)

        self.is_playing = True
        self.player.playlist_pos = 0 # Start playing from the first item
        
    def pause(self) -> None:
        """Pauses playback."""
        self.player.pause = True

    def resume(self) -> None:
        """Resumes playback."""
        self.player.pause = False

    def stop(self) -> None:
        """Stops playback and clears the playlist."""
        if self.is_playing:
            self.is_playing = False
            self.player.playlist_clear()
            self.player.command("stop")
            self._run_done_callback()
            
    def set_volume(self, volume: int) -> None:
        """Sets the player volume from 0 to 100."""
        try:
            self.player.volume = max(0, min(100, volume))
        except Exception:
            _LOGGER.exception("set_volume() failed")

    def duck(self, target_percent: int = 20) -> None:
        """Lowers the volume for an announcement."""
        if self._pre_duck_volume is not None:
            return
        try:
            self._pre_duck_volume = int(self.player.volume)
            self.set_volume(target_percent)
        except Exception:
            _LOGGER.exception("duck() failed")

    def unduck(self) -> None:
        """Restores the volume after an announcement."""
        if self._pre_duck_volume is None:
            return
        try:
            self.set_volume(self._pre_duck_volume)
        finally:
            self._pre_duck_volume = None

    def _on_idle_active(self, _name: str, active: bool) -> None:
        """Callback triggered when mpv enters or leaves the idle state."""
        if active and self.is_playing:
            _LOGGER.debug("mpv became idle; treating as end-of-playback")
            self.is_playing = False
            self._run_done_callback()

    def _run_done_callback(self) -> None:
        """Safely runs the done_callback if it exists."""
        with self._done_callback_lock:
            cb = self._done_callback
            self._done_callback = None
        if cb:
            try:
                cb()
            except Exception:
                _LOGGER.exception("Error in done_callback")

    def _mpv_log(self, level: str, prefix: str, text: str) -> None:
        """Routes mpv's internal logs to our logger."""
        msg = f"mpv[{prefix}]: {text}".rstrip()
        if level == "error":
            _LOGGER.error(msg)
        elif level == "warn":
            _LOGGER.warning(msg)
        elif level == "info":
            _LOGGER.info(msg)
        else:
            _LOGGER.debug(msg)
