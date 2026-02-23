from abc import abstractmethod
from collections.abc import Iterable
from typing import Callable, List, Optional, Union
import logging

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
    EntityCategory,
    MediaPlayerCommand,
    MediaPlayerEntityFeature,
    MediaPlayerState,
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
        initial_volume: float = 1.0,
        on_volume_changed: Optional[Callable[[float], None]] = None,
    ) -> None:
        ESPHomeEntity.__init__(self, server)

        self.key = key
        self.name = name
        self.object_id = object_id
        self.state = MediaPlayerState.IDLE
        self.volume = max(0.0, min(1.0, initial_volume))
        self.muted = False
        self.previous_volume = 1.0
        self.music_player = music_player
        self.announce_player = announce_player
        self._on_volume_changed = on_volume_changed
        self.apply_volume_from_state(initial_volume)         
        self._log = logging.getLogger(f"{self.__class__.__name__}[{self.key}]")

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
        self._log.debug("handle_message called with msg: %s", msg)

        if isinstance(msg, MediaPlayerCommandRequest) and (msg.key == self.key):
            self._log.debug("MediaPlayerCommandRequest matched for this key")

            if msg.has_media_url:
                self._log.debug("Message has media URL: %s", msg.media_url)
                announcement = msg.has_announcement and msg.announcement
                yield from self.play(msg.media_url, announcement=announcement)

            elif msg.has_command:
                self._log.debug("Message has command: %s", msg.command)
                command = MediaPlayerCommand(msg.command)

                if msg.command == MediaPlayerCommand.PAUSE:
                    self._log.debug("Executing PAUSE")
                    self.music_player.pause()
                    yield self._update_state(MediaPlayerState.PAUSED)

                elif msg.command == MediaPlayerCommand.PLAY:
                    self._log.debug("Executing PLAY / RESUME")
                    self.music_player.resume()
                    yield self._update_state(MediaPlayerState.PLAYING)

                elif command == MediaPlayerCommand.STOP:
                    self._log.debug("Executing STOP")
                    self.music_player.stop()
                    yield self._update_state(MediaPlayerState.IDLE)

                elif command == MediaPlayerCommand.MUTE:
                    self._log.debug("Executing MUTE")
                    if not self.muted:
                        self.previous_volume = self.volume
                        self.volume = 0
                        self.music_player.set_volume(0)
                        self.announce_player.set_volume(0)
                        self.muted = True
                    yield self._update_state(self.state)

                elif command == MediaPlayerCommand.UNMUTE:
                    self._log.debug("Executing UNMUTE")
                    if self.muted:
                        self.volume = self.previous_volume
                        self.music_player.set_volume(int(self.volume * 100))
                        self.announce_player.set_volume(int(self.volume * 100))
                        self.muted = False
                    yield self._update_state(self.state)

            elif msg.has_volume:
                self._log.debug("Message has volume: %.2f", msg.volume)
                self._apply_volume(msg.volume, persist=True)
                if hasattr(self.server, "state") and getattr(self.server, "state", None) is not None:
                    self._log.debug("Persisting volume to preferences")
                    self.server.state.persist_volume(self.volume)
                else:
                    self._log.warning("Cannot persist volume - server.state not available")
                yield self._update_state(self.state)

        elif isinstance(msg, ListEntitiesRequest):
            self._log.debug("ListEntitiesRequest received")
            yield ListEntitiesMediaPlayerResponse(
                object_id=self.object_id,
                key=self.key,
                name=self.name,
                supports_pause=True,
                feature_flags=SUPPORTED_MEDIA_PLAYER_FEATURES,
            )
        elif isinstance(msg, SubscribeHomeAssistantStatesRequest):
            self._log.debug("SubscribeHomeAssistantStatesRequest received")
            yield self._get_state_message()
        else:
            self._log.warning("Unknown message type received: %s", type(msg))

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

    def apply_volume_from_state(self, volume: float) -> None:
        """Synchronize the local volume with the stored state without persisting."""

        clamped = max(0.0, min(1.0, float(volume)))

        if self.muted:
            self.previous_volume = clamped
            return

        self._apply_volume(clamped, persist=False)

    def set_volume_callback(self, callback: Optional[Callable[[float], None]]) -> None:
        """Update the callback invoked when the volume changes."""

        self._on_volume_changed = callback

    def _apply_volume(
        self,
        volume: float,
        *,
        persist: bool,
        remember: bool = True,
    ) -> None:
        normalized = max(0.0, min(1.0, float(volume)))
        volume_percent = int(round(normalized * 100))

        self.music_player.set_volume(volume_percent)
        self.announce_player.set_volume(volume_percent)

        self.volume = normalized

        if remember:
            self.previous_volume = normalized

        if self._on_volume_changed and persist:
            self._on_volume_changed(normalized)

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
        self._switch_state = (
            self._get_muted()
        )  # Sync internal state with actual muted value on init

    def update_set_muted(self, set_muted: Callable[[bool], None]) -> None:
        # Update the callback used to change the mute state.
        self._set_muted = set_muted

    def update_get_muted(self, get_muted: Callable[[], bool]) -> None:
        # Update the callback used to read the mute state.
        self._get_muted = get_muted

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


class ThinkingSoundEntity(ESPHomeEntity):
    def __init__(
        self,
        server: APIServer,
        key: int,
        name: str,
        object_id: str,
        get_thinking_sound_enabled: Callable[[], bool],
        set_thinking_sound_enabled: Callable[[bool], None],
    ) -> None:
        ESPHomeEntity.__init__(self, server)

        self.key = key
        self.name = name
        self.object_id = object_id
        self._get_thinking_sound_enabled = get_thinking_sound_enabled
        self._set_thinking_sound_enabled = set_thinking_sound_enabled
        self._switch_state = self._get_thinking_sound_enabled()  # Sync internal state

    def update_get_thinking_sound_enabled(
        self, get_thinking_sound_enabled: Callable[[], bool]
    ) -> None:
        # Update the callback used to read the thinking sound enabled state.
        self._get_thinking_sound_enabled = get_thinking_sound_enabled

    def update_set_thinking_sound_enabled(
        self, set_thinking_sound_enabled: Callable[[bool], None]
    ) -> None:
        # Update the callback used to change the thinking sound enabled state.
        self._set_thinking_sound_enabled = set_thinking_sound_enabled

    def sync_with_state(self) -> None:
        # Sync internal switch state with the actual thinking sound enabled state.
        self._switch_state = self._get_thinking_sound_enabled()

    def handle_message(self, msg: message.Message) -> Iterable[message.Message]:
        if isinstance(msg, SwitchCommandRequest) and (msg.key == self.key):
            # User toggled the switch - update our internal state and trigger actions
            new_state = bool(msg.state)
            self._switch_state = new_state
            self._set_thinking_sound_enabled(new_state)
            # Return the new state immediately
            yield SwitchStateResponse(key=self.key, state=self._switch_state)
        elif isinstance(msg, ListEntitiesRequest):
            yield ListEntitiesSwitchResponse(
                object_id=self.object_id,
                key=self.key,
                name=self.name,
                entity_category=EntityCategory.CONFIG,
                icon="mdi:music-note",
            )
        elif isinstance(msg, SubscribeHomeAssistantStatesRequest):
            # Always return our internal switch state
            self.sync_with_state()
            yield SwitchStateResponse(key=self.key, state=self._switch_state)
