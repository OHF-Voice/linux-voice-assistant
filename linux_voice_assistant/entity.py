from abc import abstractmethod
from collections.abc import Iterable
from typing import TYPE_CHECKING, Callable, List, Optional, Union

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

if TYPE_CHECKING:
    from .sendspin_bridge import SendspinBridge


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
        self.sendspin_bridge: Optional["SendspinBridge"] = None

    def set_sendspin_bridge(self, bridge: "SendspinBridge") -> None:
        """Set the SendSpin bridge for coordinated playback."""
        self.sendspin_bridge = bridge
        # Register callback so SendSpin can notify us when it starts
        bridge.set_on_sendspin_start(self._on_sendspin_start)

    def _on_sendspin_start(self) -> None:
        """Called when SendSpin starts playing - pause HA music and report paused."""
        if self.music_player.is_playing:
            self.music_player.pause()
        # Report PAUSED to HA since HA's music is paused (SendSpin is playing)
        self.server.send_messages([self._update_state(MediaPlayerState.PAUSED)])

    def _stop_sendspin_if_playing(self) -> None:
        """Stop SendSpin playback if it's active."""
        if self.sendspin_bridge and self.sendspin_bridge.is_playing:
            self.sendspin_bridge.stop()

    def _pause_sendspin_if_playing(self) -> bool:
        """Pause SendSpin playback if it's active. Returns True if paused."""
        if self.sendspin_bridge and self.sendspin_bridge.is_playing:
            self.sendspin_bridge.pause()
            return True
        return False

    def _resume_sendspin(self) -> None:
        """Resume SendSpin playback if it was paused."""
        if self.sendspin_bridge:
            self.sendspin_bridge.resume()

    def play(
        self,
        url: Union[str, List[str]],
        announcement: bool = False,
        done_callback: Optional[Callable[[], None]] = None,
    ) -> Iterable[message.Message]:
        sendspin_was_playing = False

        if announcement:
            # For announcements, pause SendSpin (don't stop it) so it can resume after
            sendspin_was_playing = self._pause_sendspin_if_playing()
        else:
            # For music playback, stop SendSpin completely (HA is taking over)
            self._stop_sendspin_if_playing()

        if announcement:
            if self.music_player.is_playing:
                # HA music playing: pause it, play announcement, then resume both
                self.music_player.pause()
                self.announce_player.play(
                    url,
                    done_callback=lambda: call_all(
                        self.music_player.resume,
                        self._resume_sendspin if sendspin_was_playing else lambda: None,
                        done_callback,
                    ),
                )
            elif sendspin_was_playing:
                # SendSpin was playing: play announcement, then resume SendSpin
                self.announce_player.play(
                    url,
                    done_callback=lambda: call_all(
                        self._resume_sendspin,
                        lambda: self.server.send_messages(
                            [self._update_state(MediaPlayerState.PAUSED)]
                        ),
                        done_callback,
                    ),
                )
            else:
                # Nothing was playing, just announce then go idle
                self.announce_player.play(
                    url,
                    done_callback=lambda: call_all(
                        lambda: self.server.send_messages(
                            [self._update_state(MediaPlayerState.IDLE)]
                        ),
                        done_callback,
                    ),
                )
        else:
            # Music playback
            self.music_player.play(
                url,
                done_callback=lambda: call_all(
                    lambda: self.server.send_messages(
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
                # Sync volume with SendSpin bridge
                if self.sendspin_bridge:
                    self.sendspin_bridge.set_volume(volume, self.muted)
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
