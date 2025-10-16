"""Media player using mpv in a subprocess."""

import logging
from collections.abc import Callable
from threading import Lock
from typing import List, Optional, Union

from mpv import MPV

_LOGGER = logging.getLogger(__name__)


class MpvMediaPlayer:
    def __init__(self, device: Optional[str] = None) -> None:
        self.player = MPV()

        if device:
            self.player["audio-device"] = device

        self.is_playing = False

        self._playlist: List[str] = []
        self._done_callback: Optional[Callable[[], None]] = None
        self._done_callback_lock = Lock()

        self._duck_ratio: float = 0.2
        self._unduck_volume: int = 100
        self._duck_volume: int = self._compute_duck_volume()

        self.player.event_callback("end-file")(self._on_end_file)

    def play(
        self,
        url: Union[str, List[str]],
        done_callback: Optional[Callable[[], None]] = None,
        stop_first: bool = True,
    ) -> None:
        self.stop()

        if isinstance(url, str):
            self._playlist = [url]
        else:
            self._playlist = url

        next_url = self._playlist.pop(0)
        _LOGGER.debug("Playing %s", next_url)

        self._done_callback = done_callback
        self.is_playing = True
        self.player.play(next_url)

    def pause(self) -> None:
        self.player.pause = True
        self.is_playing = False

    def resume(self) -> None:
        self.player.pause = False
        if self._playlist:
            self.is_playing = True

    def stop(self) -> None:
        self.player.stop()
        self._playlist.clear()

    def duck(self) -> None:
        self.player.volume = self._duck_volume

    def unduck(self) -> None:
        self.player.volume = self._unduck_volume

    def set_volume(self, volume: int) -> None:
        volume = max(0, min(100, volume))
        self.player.volume = volume

        self._unduck_volume = volume
        self._unduck_volume = volume
        self._duck_volume = self._compute_duck_volume()

    def _compute_duck_volume(self) -> int:
        if self._unduck_volume <= 0:
            return 0

        return max(1, int(round(self._unduck_volume * self._duck_ratio)))

    def _on_end_file(self, event) -> None:
        if self._playlist:
            self.player.play(self._playlist.pop(0))
            return

        self.is_playing = False

        todo_callback: Optional[Callable[[], None]] = None
        with self._done_callback_lock:
            if self._done_callback:
                todo_callback = self._done_callback
                self._done_callback = None

        if todo_callback:
            try:
                todo_callback()
            except Exception:
                _LOGGER.exception("Unexpected error running done callback")
