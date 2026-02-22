# mpv_player.py
import logging
from typing import Union, List, Callable, Optional

from .player.libmpv import LibMpvPlayer
from .player.state import PlayerState


class MpvMediaPlayer:
    """
    Linux Voice Assistant MediaPlayer implementation based on libmpv.

    This class provides the MediaPlayer interface expected by LVA and
    delegates all playback logic to LibMpvPlayer.
    """

    def __init__(self, device: str | None = None) -> None:
        self._log = logging.getLogger(self.__class__.__name__)
        self._player = LibMpvPlayer(device=device)
        self._done_callback: Optional[Callable[[], None]] = None

        self._log.debug("MpvMediaPlayer initialized (device=%s)", device)

    def play(
        self,
        url: Union[str, List[str]],
        done_callback: Optional[Callable[[], None]] = None,
        stop_first: bool = False,
    ) -> None:
        """
        Play a media URL.

        Args:
            url: Media URL or list of URLs (LVA currently uses a single URL).
            done_callback: Optional callback invoked when playback finishes.
            stop_first: Kept for API compatibility.
        """
        # LVA currently only uses single URLs
        if isinstance(url, list):
            self._log.debug("Received URL list, using first entry")
            url = url[0]

        # Track is changing - only stop for replacement if actually playing
        if self._done_callback is not None:
            if self._player.state() != PlayerState.IDLE:
                self._log.debug(
                    "Stopping active playback before starting new media"
                )
                self._player.stop(for_replacement=True)
            self._done_callback = None

        self._log.info("Playing media: %s", url)
        self._log.debug(
            "play(url=%s, stop_first=%s, done_callback=%s)",
            url,
            stop_first,
            bool(done_callback),
        )

        self._done_callback = done_callback
        self._player.play(url, done_callback=done_callback, stop_first=stop_first)

    def pause(self) -> None:
        """Pause playback."""
        self._log.debug("pause() called")
        self._player.pause()

    def resume(self) -> None:
        """Resume playback."""
        self._log.debug("resume() called")
        self._player.resume()

    def stop(self) -> None:
        """Stop playback and invoke the done callback if present."""
        self._log.debug("stop() called")

        self._player.stop()

        if self._done_callback:
            self._log.debug("Invoking done_callback due to stop()")
            try:
                self._done_callback()
            finally:
                self._done_callback = None

    def set_volume(self, volume: float) -> None:
        """
        Set playback volume.

        Args:
            volume: Volume in percent (0.0-100.0).
        """
        self._log.debug("set_volume(volume=%.2f)", volume)
        self._player.set_volume(volume)

    def duck(self, factor: float = 0.5) -> None:
        """
        Temporarily reduce volume.

        Args:
            factor: Volume multiplier (0.0-1.0).
        """
        self._log.debug("duck(factor=%.2f)", factor)
        self._player.duck(factor)

    def unduck(self) -> None:
        """Restore volume after ducking."""
        self._log.debug("unduck() called")
        self._player.unduck()
