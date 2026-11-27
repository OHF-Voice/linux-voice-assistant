"""
Creates and starts the Sendspin bridge: the client of the control socket and, in ``pulse`` duck mode, the ducker of the sound stream.

Kept out of ``__main__.py`` so that the start of LVA only needs one call before and one after the server state is built.
"""

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Tuple

from .pulse_stream_ducker import SENDSPIN_APPLICATION_NAME, PulseStreamDucker, StreamDuck
from .sendspin_player import CONTROL_SOCKET_NAME, DUCK_MODE_PULSE, SendspinPlayer

if TYPE_CHECKING:
    from .models import Preferences, ServerState

_LOGGER = logging.getLogger(__name__)


def create_sendspin_player(enabled: bool, control_dir: str, duck_mode: str, preferences: "Preferences") -> Tuple[Optional[SendspinPlayer], Optional[PulseStreamDucker]]:
    """
    Create the client of the Sendspin player.
    :param enabled: Whether the Sendspin bridge is on (``--enable-sendspin-bridge``).
    :param control_dir: Directory that holds ``control.sock`` (``--sendspin-control-dir``).
    :param duck_mode: ``pulse``, ``stop`` or ``duck`` (``--sendspin-player-duck-mode``).
    :param preferences: The preferences, for a ducking of the sound stream that an earlier run did not restore.
    :return: The client and the ducker of the sound stream (only in ``pulse`` mode), both None if the bridge is off.
    """
    if not enabled:
        _LOGGER.debug("Sendspin bridge is off")
        return None, None

    socket_path = Path(control_dir) / CONTROL_SOCKET_NAME
    stream_ducker: Optional[PulseStreamDucker] = None
    if duck_mode == DUCK_MODE_PULSE:
        stream_ducker = PulseStreamDucker(SENDSPIN_APPLICATION_NAME, saved=_saved_stream_duck(preferences))

    _LOGGER.debug("Sendspin bridge is on, control socket: %s, duck mode: %s", socket_path, duck_mode)
    return SendspinPlayer(socket_path, duck_mode=duck_mode, stream_ducker=stream_ducker), stream_ducker


def start_sendspin_bridge(state: "ServerState", stream_ducker: Optional[PulseStreamDucker]) -> Optional["asyncio.Task[None]"]:
    """
    Start polling the Sendspin player and, in ``pulse`` mode, ducking its sound stream. Must be called on the event loop.
    :param state: The server state, its ``sendspin_player`` is the client from :func:`create_sendspin_player`.
    :param stream_ducker: The ducker of the sound stream, None outside ``pulse`` mode.
    :return: The task of the stream ducker, to be cancelled on shutdown, None if there is none.
    """
    player = state.sendspin_player
    if player is None:
        return None

    def save_group_duck(volume_from: Optional[int], volume_to: Optional[int]) -> None:
        state.preferences.sendspin_ducked_from = volume_from
        state.preferences.sendspin_ducked_to = volume_to
        state.preferences.save(state.preferences_path)

    player.set_duck_persistence(save_group_duck, (state.preferences.sendspin_ducked_from, state.preferences.sendspin_ducked_to))
    player.start_polling(lambda status: state.media_player_entity.on_sendspin_status(status) if state.media_player_entity else None)

    if stream_ducker is None:
        return None

    def save_stream_duck(duck: Optional[StreamDuck]) -> None:
        state.preferences.sendspin_stream_ducked_from = duck.original if duck is not None else None
        state.preferences.sendspin_stream_ducked_to = duck.ducked if duck is not None else None
        state.preferences.save(state.preferences_path)

    return stream_ducker.start(save_stream_duck)


def _saved_stream_duck(preferences: "Preferences") -> Optional[StreamDuck]:
    if preferences.sendspin_stream_ducked_from is None or preferences.sendspin_stream_ducked_to is None:
        return None
    return StreamDuck(original=preferences.sendspin_stream_ducked_from, ducked=preferences.sendspin_stream_ducked_to)
