# mpv_player.py
import logging
import threading
from typing import Callable, List, Optional, Set, Union

from .player.libmpv import LibMpvPlayer
from .player.state import PlayerState

_DUCK_OWNERS = ("conversation", "timer", "announce")
_DUCK_MODES = ("duck", "pause")


class MpvMediaPlayer:
    """
    Linux Voice Assistant MediaPlayer implementation based on libmpv.

    This class provides the MediaPlayer interface expected by LVA and
    delegates all playback logic to LibMpvPlayer.
    """

    def __init__(self, device: str | None = None, name: str = "mpv", duck_mode: str = "duck") -> None:
        """
        :param device: mpv audio device name, None for the default.
        :param name: Name of the player in the log, for example ``music`` or ``tts``.
        :param duck_mode: ``duck`` lowers the volume while ducked, ``pause`` pauses playback and resumes it afterwards.
        :raises ValueError: If the duck mode is not a known one.
        """
        if duck_mode not in _DUCK_MODES:
            raise ValueError(f"Unknown duck mode {duck_mode!r}, expected one of {_DUCK_MODES}")
        self._log = logging.getLogger(f"{self.__class__.__name__}[{name}]")
        self._player = LibMpvPlayer(device=device, name=name)
        self._done_callback: Optional[Callable[[], None]] = None
        self._playlist: List[str] = []
        self._duck_lock = threading.Lock()
        self._duck_owners: Set[str] = set()
        self._duck_mode = duck_mode
        self._paused_by_duck = False

        self._log.debug("MpvMediaPlayer initialized (device=%s, duck mode=%s)", device, duck_mode)

    def play(
        self,
        url: Union[str, List[str]],
        done_callback: Optional[Callable[[], None]] = None,
        stop_first: bool = False,
    ) -> None:
        """
        Play a media URL.
        :param url: Media URL or list of URLs for sequential playback.
        :param done_callback: Optional callback invoked when playback finishes.
        :param stop_first: Kept for API compatibility.
        """
        # Handle single URL vs list
        if isinstance(url, str):
            urls = [url]
        else:
            urls = list(url)  # Copy the list

        if not urls:
            self._log.warning("play() called with empty URL list")
            return

        # Track is changing - stop if needed
        if self._done_callback is not None:
            if self._player.state() != PlayerState.IDLE:
                self._log.debug("Stopping active playback before starting new media")
                self._player.stop(for_replacement=True)
            self._done_callback = None

        self._log.info("Playing %d URL(s): %s", len(urls), urls[0])
        self._release_pause_by_duck()

        # Store playlist and callback
        self._playlist = urls
        self._done_callback = done_callback

        # Start playing first URL
        next_url = self._playlist.pop(0)
        self._player.play(next_url, done_callback=self._on_track_finished, stop_first=stop_first)

    def _on_track_finished(self) -> None:
        """Called when a track finishes - plays next or invokes done callback."""
        if self._playlist:
            # More tracks to play
            next_url = self._playlist.pop(0)
            self._log.debug("Playing next URL from playlist: %s", next_url)
            self._player.play(next_url, done_callback=self._on_track_finished, stop_first=False)
        else:
            # Playlist finished
            callback = self._done_callback
            self._done_callback = None

            if callback:
                self._log.debug("Playlist finished, invoking done_callback")
                try:
                    callback()
                except Exception as e:
                    self._log.exception("Error in done_callback: %s", e)

    def pause(self) -> None:
        """Pause playback."""
        self._log.debug("pause() called")
        self._release_pause_by_duck()
        self._player.pause()

    def resume(self) -> None:
        """Resume playback."""
        self._log.debug("resume() called")
        self._release_pause_by_duck()
        self._player.resume()

    def stop(self) -> None:
        """Stop playback and invoke the done callback if present."""
        self._log.debug("stop() called")
        self._release_pause_by_duck()

        self._player.stop()

        if self._done_callback:
            self._log.debug("Invoking done_callback due to stop()")
            try:
                self._done_callback()
            finally:
                self._done_callback = None

    @property
    def is_playing(self) -> bool:
        """Check if the player is currently playing or paused."""
        state = self._player.state()
        return state in (PlayerState.PLAYING, PlayerState.PAUSED, PlayerState.LOADING)

    @property
    def is_paused(self) -> bool:
        """Check if the player is currently paused. A pause that only serves ducking does not count."""
        return self._player.state() == PlayerState.PAUSED and not self._paused_by_duck

    def set_volume(self, volume: float) -> None:
        """
        Set playback volume.
        :param volume: Volume in percent (0.0-100.0).
        """
        self._player.set_volume(volume)

    def duck(self, owner: str, factor: float = 0.5) -> None:
        """
        Temporarily reduce volume, or pause playback in ``pause`` mode.
        :param owner: Who asks: ``conversation``, ``timer`` or ``announce``. The volume is restored when every owner has called :meth:`unduck`.
        :param factor: Volume multiplier (0.0-1.0), not used in ``pause`` mode.
        :raises ValueError: If the owner is not a known one.
        """
        self._check_duck_owner(owner)
        with self._duck_lock:
            self._duck_owners.add(owner)
            self._log.debug("Duck requested by %s, factor %.2f (owners: %s)", owner, factor, sorted(self._duck_owners))
            if self._duck_mode == "pause":
                if self._player.state() == PlayerState.PLAYING and not self._paused_by_duck:
                    self._log.debug("Pausing instead of ducking")
                    self._paused_by_duck = True
                    self._player.pause()
            else:
                self._player.duck(factor)

    def unduck(self, owner: str) -> None:
        """
        Restore volume (or resume playback in ``pause`` mode) after ducking, once no owner wants it ducked any more.
        :param owner: The owner that called :meth:`duck`.
        :raises ValueError: If the owner is not a known one.
        """
        self._check_duck_owner(owner)
        with self._duck_lock:
            self._duck_owners.discard(owner)
            if self._duck_owners:
                self._log.debug("Unduck by %s, stays ducked for %s", owner, sorted(self._duck_owners))
            else:
                self._log.debug("Unduck by %s, no owner left", owner)
                if self._duck_mode == "pause":
                    if self._paused_by_duck:
                        self._paused_by_duck = False
                        if self._player.state() == PlayerState.PAUSED:
                            self._log.debug("Resuming playback paused for ducking")
                            self._player.resume()
                else:
                    self._player.unduck()

    def _release_pause_by_duck(self) -> None:
        """A command from outside takes over a pause that only served ducking, so the unduck does not undo it."""
        with self._duck_lock:
            self._paused_by_duck = False

    def _check_duck_owner(self, owner: str) -> None:
        if owner not in _DUCK_OWNERS:
            raise ValueError(f"Unknown duck owner {owner!r}, expected one of {_DUCK_OWNERS}")
