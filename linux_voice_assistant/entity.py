from abc import abstractmethod
from collections.abc import Iterable
from typing import Callable, List, Optional, Union

# pylint: disable=no-name-in-module
from aioesphomeapi.api_pb2 import (  # type: ignore[attr-defined]
    ListEntitiesMediaPlayerResponse,
    ListEntitiesRequest,
    ListEntitiesSwitchResponse,
    MediaPlayerCommandRequest,
    MediaPlayerStateResponse,
    SwitchCommandRequest,
    SwitchStateResponse,
    SubscribeHomeAssistantStatesRequest,
)
from aioesphomeapi.model import MediaPlayerCommand, MediaPlayerState
from google.protobuf import message

from .api_server import APIServer
from .mpv_player import MpvMediaPlayer
from .util import call_all


class ESPHomeEntity:
    def __init__(self, server: APIServer) -> None:
        self.server = server

    @abstractmethod
    def handle_message(self, msg: message.Message) -> Iterable[message.Message]:
        pass


# -----------------------------------------------------------------------------


class MediaPlayerEntity(ESPHomeEntity):
    def __init__(
        self,
        server: APIServer,
        key: int,
        name: str,
        object_id: str,
        music_player: MpvMediaPlayer,
        announce_player: MpvMediaPlayer,
    ) -> None:
        ESPHomeEntity.__init__(self, server)

        self.key = key
        self.name = name
        self.object_id = object_id
        self.state = MediaPlayerState.IDLE
        self.volume = 1.0
        self.muted = False
        self.music_player = music_player
        self.announce_player = announce_player

    def play(
        self,
        url: Union[str, List[str]],
        announcement: bool = False,
        done_callback: Optional[Callable[[], None]] = None,
    ) -> Iterable[message.Message]:
        if announcement:
            if self.music_player.is_playing:
                # Announce, resume music
                self.music_player.pause()
                self.announce_player.play(
                    url,
                    done_callback=lambda: call_all(
                        self.music_player.resume, done_callback
                    ),
                )
            else:
                # Announce, idle
                self.announce_player.play(
                    url,
                    done_callback=lambda: call_all(
                        self.server.send_messages(
                            [self._update_state(MediaPlayerState.IDLE)]
                        ),
                        done_callback,
                    ),
                )
        else:
            # Music
            self.music_player.play(
                url,
                done_callback=lambda: call_all(
                    self.server.send_messages(
                        [self._update_state(MediaPlayerState.IDLE)]
                    ),
                    done_callback,
                ),
            )

        yield self._update_state(MediaPlayerState.PLAYING)

    def handle_message(self, msg: message.Message) -> Iterable[message.Message]:
        if isinstance(msg, MediaPlayerCommandRequest) and (msg.key == self.key):
            if msg.has_media_url:
                announcement = msg.has_announcement and msg.announcement
                yield from self.play(msg.media_url, announcement=announcement)
            elif msg.has_command:
                if msg.command == MediaPlayerCommand.PAUSE:
                    self.music_player.pause()
                    yield self._update_state(MediaPlayerState.PAUSED)
                elif msg.command == MediaPlayerCommand.PLAY:
                    self.music_player.resume()
                    yield self._update_state(MediaPlayerState.PLAYING)
            elif msg.has_volume:
                volume = int(msg.volume * 100)
                self.music_player.set_volume(volume)
                self.announce_player.set_volume(volume)
                self.volume = msg.volume
                yield self._update_state(self.state)
        elif isinstance(msg, ListEntitiesRequest):
            yield ListEntitiesMediaPlayerResponse(
                object_id=self.object_id,
                key=self.key,
                name=self.name,
                supports_pause=True,
            )
        elif isinstance(msg, SubscribeHomeAssistantStatesRequest):
            yield self._get_state_message()

    def _update_state(self, new_state: MediaPlayerState) -> MediaPlayerStateResponse:
        self.state = new_state
        return self._get_state_message()

    def _get_state_message(self) -> MediaPlayerStateResponse:
        return MediaPlayerStateResponse(
            key=self.key,
            state=self.state,
            volume=self.volume,
            muted=self.muted,
        )


# -----------------------------------------------------------------------------


class MuteSwitchEntity(ESPHomeEntity):
    """Mute switch entity that behaves like ESPHome Voice PE mute switch.
    
    This switch maintains its own state and triggers voice_assistant.stop/start_continuous actions.
    """
    
    def __init__(
        self,
        server: APIServer,
        key: int,
        name: str,
        object_id: str,
        get_muted: Callable[[], bool],
        set_muted: Callable[[bool], None],
    ) -> None:
        ESPHomeEntity.__init__(self, server)

        self.key = key
        self.name = name
        self.object_id = object_id
        self._get_muted = get_muted
        self._set_muted = set_muted
        self._switch_state = False  # Internal switch state

    def handle_message(self, msg: message.Message) -> Iterable[message.Message]:
        if isinstance(msg, SwitchCommandRequest) and (msg.key == self.key):
            # User toggled the switch - update our internal state and trigger actions
            new_state = bool(msg.state)
            self._switch_state = new_state
            self._set_muted(new_state)
            # Return the new state immediately
            yield SwitchStateResponse(key=self.key, state=self._switch_state)
        elif isinstance(msg, ListEntitiesRequest):
            yield ListEntitiesSwitchResponse(
                object_id=self.object_id,
                key=self.key,
                name=self.name,
            )
        elif isinstance(msg, SubscribeHomeAssistantStatesRequest):
            # Always return our internal switch state
            yield SwitchStateResponse(key=self.key, state=self._switch_state)
