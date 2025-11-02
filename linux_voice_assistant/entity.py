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
    SubscribeHomeAssistantStatesRequest,
    SwitchCommandRequest,
    SwitchStateResponse,
)
from aioesphomeapi.model import (
    MediaPlayerCommand,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    EntityCategory,
)
from google.protobuf import message

from .api_server import APIServer
from .mpv_player import MpvMediaPlayer
from .util import call_all


SUPPORTED_MEDIA_PLAYER_FEATURES = (
    MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.MEDIA_ANNOUNCE
)


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
        self.previous_volume = 1.0
        self.music_player = music_player
        self.announce_player = announce_player

    def play(
        self,
        url: Union[str, List[str]],
        announcement: bool = False,
        done_callback: Optional[Callable[[], None]] = None,
    ) -> Iterable[message.Message]:
        previous_state = self._determine_state()

        if announcement:
            music_was_playing = self.music_player.is_playing
            announce_was_paused = self.announce_player.is_paused

            if music_was_playing:
                self.music_player.pause()

            def restore_announcement_pause() -> None:
                if announce_was_paused:
                    self.announce_player.pause()
                    self.announce_player.is_paused = True

            def restore_state() -> None:
                self.server.send_messages(
                    [self._update_state(previous_state)]
                )

            if music_was_playing:
                self.announce_player.play(
                    url,
                    done_callback=lambda: call_all(
                        self.music_player.resume,
                        restore_announcement_pause,
                        restore_state,
                        done_callback,
                    ),
                )
            else:
                self.announce_player.play(
                    url,
                    done_callback=lambda: call_all(
                        restore_announcement_pause,
                        restore_state,
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
                command = MediaPlayerCommand(msg.command)
                if command == MediaPlayerCommand.PAUSE:
                    self.music_player.pause()
                    self.announce_player.pause()
                    yield self._update_state(self._determine_state())
                elif command == MediaPlayerCommand.PLAY:
                    self.music_player.resume()
                    self.announce_player.resume()
                    yield self._update_state(self._determine_state())
                elif command == MediaPlayerCommand.STOP:
                    self.music_player.stop()
                    self.announce_player.stop()
                    yield self._update_state(self._determine_state())
                elif command == MediaPlayerCommand.MUTE:
                    if not self.muted:
                        self.previous_volume = self.volume
                        self.volume = 0
                        self.music_player.set_volume(0)
                        self.announce_player.set_volume(0)
                        self.muted = True
                    yield self._update_state(self.state)
                elif command == MediaPlayerCommand.UNMUTE:
                    if self.muted:
                        self.volume = self.previous_volume
                        self.music_player.set_volume(int(self.volume * 100))
                        self.announce_player.set_volume(int(self.volume * 100))
                        self.muted = False
                    yield self._update_state(self.state)
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
                feature_flags=SUPPORTED_MEDIA_PLAYER_FEATURES,
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

    def _determine_state(self) -> MediaPlayerState:
        if self.music_player.is_playing or self.announce_player.is_playing:
            return MediaPlayerState.PLAYING

        if self.music_player.is_paused or self.announce_player.is_paused:
            return MediaPlayerState.PAUSED

        return MediaPlayerState.IDLE


# -----------------------------------------------------------------------------


class MuteSwitchEntity(ESPHomeEntity):
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
        self._switch_state = self._get_muted()  # Sync internal state with actual muted value on init

    def update_get_muted(self, get_muted: Callable[[], bool]) -> None:
        # Update the callback used to read the mute state.
        self._get_muted = get_muted

    def update_set_muted(self, set_muted: Callable[[bool], None]) -> None:
        # Update the callback used to change the mute state.
        self._set_muted = set_muted

    def sync_with_state(self) -> None:
        # Sync internal switch state with the actual mute state.
        self._switch_state = self._get_muted()

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
                entity_category=EntityCategory.CONFIG,
                icon="mdi:microphone-off",
            )
        elif isinstance(msg, SubscribeHomeAssistantStatesRequest):
            # Always return our internal switch state
            self.sync_with_state()
            yield SwitchStateResponse(key=self.key, state=self._switch_state)
