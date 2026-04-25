import asyncio
import json
import logging
import re
import threading
from abc import abstractmethod
from collections.abc import Iterable
import subprocess
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


async def get_stdout(*args):
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        stdin=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    return stdout.decode('utf-8').strip()

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

        if hasattr(self.server, "state") and self.server.state.volume_controller in ["pipewire", "pulseaudio"]:
            asyncio.get_running_loop().create_task(self.volume_monitor_loop())

    async def pw_vol(self):
        # a bit verbose but otherwise robust and awk/grep/regex-less
        # no get-sink-volume does not support -f json, otherwise i would have DONE that
        def_sink = await get_stdout("pactl", "get-default-sink")
        sinks = json.loads(await get_stdout("pactl", "-f", "json", "list", "sinks"))

        def_sink_info = None

        for sink in sinks:
            if sink.get("name") == def_sink:
                def_sink_info = sink
                break
        else:
            def_sink_info = sinks[0]

        volumes = []
        for volume in def_sink_info["volume"].values():
            volumes.append(float(volume["value_percent"].replace("%", "")))


        vol = sum(volumes) / len(volumes)

        return vol

    async def volume_monitor_loop(self):
        while True:
            try:
                process = await asyncio.create_subprocess_exec(
                    "pactl", "-f", "json", "subscribe",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    stdin=asyncio.subprocess.DEVNULL,
                )
                while True:
                    # 2. Thread sleeps here until PipeWire actually changes state
                    line = await process.stdout.readline()

                    line = json.loads(line.decode('utf-8').strip())

                    if line.get("event") == "change" and line.get("on") == "sink":
                        self._log.debug(f"pactl subscribe event {line}")
                        volume = await self.pw_vol()
                        self._log.debug(f"new volume: {volume}")

                        normalized = max(0.0, min(1.0, float(volume)))

                        self.volume = normalized
                        self.previous_volume = normalized

                        if self._on_volume_changed:
                            self._on_volume_changed(normalized)

                        self.server.state.persist_volume(normalized)

                        self.server.send_messages([self._get_state_message()])

            except Exception as e:
                self._log.error("Error in volume monitor loop: %s", e, e.__traceback__)
                await asyncio.sleep(1)  # Avoid tight error loop

    def set_volume(self, volume: float) -> None:
        # self._log.debug("Setting volume: %.2f", volume)
        self.volume = volume
        if hasattr(self.server, "state") and self.server.state.volume_controller in ["pipewire", "pulseaudio"]:
            def _update_system_vol():
                try:
                    # vol_percent = f"{int(round(volume * 100))}%"
                    self._log.debug("pactl start")
                    res = subprocess.run(
                        ["pactl", "set-sink-volume", self.server.state.audio_output_device or "@DEFAULT_SINK@", str(volume)],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=1
                    )
                    if res.returncode == 0:
                        self._log.debug("pactl success")
                    else:
                        self._log.error("pactl failed with error code: %s", res.returncode)
                except Exception as e:
                    self._log.error("pactl err ", e)
                    # self._log.error("Volume command failed: %s", e)

            threading.Thread(target=_update_system_vol, daemon=True).start()
        else:
            percent = int(round(volume * 100))
            self.music_player.set_volume(percent)
            self.announce_player.set_volume(percent)

    def play(
        self,
        url: Union[str, List[str]],
        announcement: bool = False,
        done_callback: Optional[Callable[[], None]] = None,
    ) -> Iterable[message.Message]:
        if announcement:
            self._log.debug("PLAY: announcement true")
            if self.music_player.is_playing:
                # Announce, resume music
                self.music_player.pause()
                self.announce_player.play(
                    url,
                    done_callback=lambda: call_all(self.music_player.resume, done_callback),
                )
            else:
                # Announce, idle
                self.announce_player.play(
                    url,
                    done_callback=lambda: call_all(
                        self.server.send_messages([self._update_state(MediaPlayerState.IDLE)]),
                        done_callback,
                    ),
                )
        else:
            self._log.debug("PLAY: announcement false")
            # Music
            self.music_player.play(
                url,
                done_callback=lambda: call_all(
                    self.server.send_messages([self._update_state(MediaPlayerState.IDLE)]),
                    done_callback,
                ),
            )

        yield self._update_state(MediaPlayerState.PLAYING)

    def handle_message(self, msg: message.Message) -> Iterable[message.Message]:
        self._log.debug("handle_message called with msg: %s", msg)

        if isinstance(msg, MediaPlayerCommandRequest) and (msg.key == self.key):
            self._log.debug("MediaPlayerCommandRequest matched for this key")

            if msg.has_media_url:
                self._log.debug("Executing PLAY")
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
                        self.set_volume(0)
                        self.muted = True
                    yield self._update_state(self.state)

                elif command == MediaPlayerCommand.UNMUTE:
                    self._log.debug("Executing UNMUTE")
                    if self.muted:
                        self.set_volume(self.previous_volume)
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
        self._log.debug("SET NEW STATE: %s => %s", self.state, new_state)
        self._log.debug("SET NEW STATE: %s => %s", self.state.name, new_state.name)
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

        self.set_volume(normalized)

        # self.volume = normalized

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
        self._switch_state = self._get_muted()  # Sync internal state with actual muted value on init

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

    def update_get_thinking_sound_enabled(self, get_thinking_sound_enabled: Callable[[], bool]) -> None:
        # Update the callback used to read the thinking sound enabled state.
        self._get_thinking_sound_enabled = get_thinking_sound_enabled

    def update_set_thinking_sound_enabled(self, set_thinking_sound_enabled: Callable[[bool], None]) -> None:
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
