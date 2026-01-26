"""Bridge between SendSpin client and MediaPlayer entity."""

import asyncio
import logging
import os
import socket
import tempfile
import threading
import time
from collections import deque
from typing import TYPE_CHECKING, Callable, Optional

from aioesphomeapi.model import MediaPlayerState
from aiosendspin.client import SendspinClient
from aiosendspin.models.core import DeviceInfo, ServerCommandPayload, StreamStartMessage
from aiosendspin.models.player import ClientHelloPlayerSupport, SupportedAudioFormat
from aiosendspin.models.types import AudioCodec, PlayerCommand, PlayerStateType, Roles
from mpv import MPV

if TYPE_CHECKING:
    from aiosendspin.client import AudioFormat

    from .entity import MediaPlayerEntity

_LOGGER = logging.getLogger(__name__)


class SendspinBridge:
    """Bridge that connects SendSpin streaming to MediaPlayerEntity.

    When SendSpin server starts streaming, this bridge plays the audio using
    MPV with a FIFO pipe. Audio chunks are written immediately to prevent
    starvation, with the initial buffering delay handled by waiting to start
    the stream.
    """

    def __init__(
        self,
        media_player_entity: "MediaPlayerEntity",
        client_id: Optional[str] = None,
        client_name: Optional[str] = None,
        static_delay_ms: float = 0.0,
        audio_device: Optional[str] = None,
    ) -> None:
        """Initialize the SendSpin bridge.

        Args:
            media_player_entity: MediaPlayerEntity to update state
            client_id: Unique client ID
            client_name: Friendly client name
            static_delay_ms: Static playback delay
            audio_device: Audio device to use (same as MPV audio-device)
        """
        hostname = socket.gethostname()
        self.media_player = media_player_entity
        self.client_id = client_id or f"linux-voice-assistant-{hostname}"
        self.client_name = client_name or hostname
        self._audio_device = audio_device

        self._client: Optional[SendspinClient] = None
        self._running = False
        self._stream_active = False

        # MPV player for SendSpin audio (similar to music_player)
        self._player: Optional[MPV] = None
        self._fifo_path: Optional[str] = None
        self._fifo_fd: Optional[int] = None
        self._current_format: Optional["AudioFormat"] = None

        # Buffering and playback
        self._buffer: deque = deque(maxlen=50)  # Buffer chunks before starting
        self._min_buffer_chunks = 10  # Start playback after 10 chunks
        self._first_chunk_timestamp: Optional[int] = None
        self._playback_started = False
        self._write_task: Optional[asyncio.Task] = None

        # Volume state (synced with MediaPlayerEntity)
        self._volume: int = 100
        self._muted: bool = False
        self._duck_volume: int = 50
        self._unduck_volume: int = 100
        self._is_ducked: bool = False

        # Callback to notify when SendSpin starts playing (so HA music can stop)
        self._on_sendspin_start: Optional[Callable[[], None]] = None

        # Create SendSpin client
        self._client = SendspinClient(
            client_id=self.client_id,
            client_name=self.client_name,
            roles=[Roles.PLAYER],
            device_info=DeviceInfo(
                product_name="Linux Voice Assistant",
                manufacturer="OHF-Voice",
                software_version="1.0.0",
            ),
            player_support=ClientHelloPlayerSupport(
                supported_formats=[
                    SupportedAudioFormat(
                        codec=AudioCodec.PCM,
                        channels=2,
                        sample_rate=44_100,
                        bit_depth=16,
                    ),
                    SupportedAudioFormat(
                        codec=AudioCodec.PCM,
                        channels=1,
                        sample_rate=44_100,
                        bit_depth=16,
                    ),
                ],
                buffer_capacity=32_000_000,
                supported_commands=[PlayerCommand.VOLUME, PlayerCommand.MUTE],
            ),
            static_delay_ms=static_delay_ms,
        )

        # Register listeners
        self._client.add_audio_chunk_listener(self._on_audio_chunk)
        self._client.add_stream_start_listener(self._on_stream_start)
        self._client.add_stream_end_listener(self._on_stream_end)
        self._client.add_server_command_listener(self._on_server_command)

    def set_on_sendspin_start(self, callback: Callable[[], None]) -> None:
        """Set callback to be called when SendSpin starts playing."""
        self._on_sendspin_start = callback

    def set_volume(self, volume: int, muted: bool = False) -> None:
        """Set volume (called from MediaPlayerEntity when HA changes volume)."""
        self._volume = max(0, min(100, volume))
        self._muted = muted
        self._unduck_volume = self._volume
        self._duck_volume = self._volume // 2
        if self._player:
            if self._is_ducked:
                self._player.volume = 0 if self._muted else self._duck_volume
            else:
                self._player.volume = 0 if self._muted else self._volume

    def duck(self) -> None:
        """Reduce volume (for wake word/TTS)."""
        self._is_ducked = True
        if self._player:
            self._player.volume = self._duck_volume

    def unduck(self) -> None:
        """Restore volume (after wake word/TTS)."""
        self._is_ducked = False
        if self._player:
            self._player.volume = 0 if self._muted else self._unduck_volume

    @property
    def is_playing(self) -> bool:
        """Check if SendSpin is currently playing."""
        return self._stream_active

    def stop(self) -> None:
        """Stop SendSpin playback (called when HA wants to play)."""
        if self._stream_active:
            _LOGGER.info("Stopping SendSpin playback (HA taking over)")
            self._stop_playback()
            self._stream_active = False

    async def start(self, server_url: Optional[str] = None) -> None:
        """Start the SendSpin client.

        Args:
            server_url: Optional server URL to connect to
        """
        if self._running or not self._client:
            return

        self._running = True

        # Sync volume with MediaPlayerEntity
        if self.media_player:
            self._volume = int(self.media_player.volume * 100)
            self._muted = self.media_player.muted

        _LOGGER.info("Starting SendSpin bridge: %s", self.client_id)

        if server_url:
            # Connect to specific server
            asyncio.create_task(self._connection_loop(server_url))
        else:
            _LOGGER.info(
                "SendSpin bridge started (no server URL - waiting for connections)"
            )

    async def disconnect(self) -> None:
        """Stop the SendSpin client."""
        if not self._running:
            return

        _LOGGER.info("Stopping SendSpin bridge")
        self._running = False

        self._stop_playback()

        if self._client and self._client.connected:
            await self._client.disconnect()

    def _create_fifo(self) -> str:
        """Create a named pipe for audio streaming."""
        # Create FIFO in temp directory
        fifo_dir = tempfile.mkdtemp(prefix="lva_sendspin_")
        fifo_path = os.path.join(fifo_dir, "audio.pcm")
        os.mkfifo(fifo_path)
        return fifo_path

    def _cleanup_fifo(self) -> None:
        """Clean up the FIFO and its directory."""
        if self._fifo_fd is not None:
            try:
                os.close(self._fifo_fd)
            except OSError:
                pass
            self._fifo_fd = None

        if self._fifo_path:
            try:
                os.unlink(self._fifo_path)
                os.rmdir(os.path.dirname(self._fifo_path))
            except OSError:
                pass
            self._fifo_path = None

    def _start_player(self, fmt: "AudioFormat") -> None:
        """Start MPV player for the given audio format."""
        self._stop_player()

        pcm_format = fmt.pcm_format
        sample_rate = pcm_format.sample_rate
        channels = pcm_format.channels

        # Create FIFO for audio data
        self._fifo_path = self._create_fifo()

        # Create MPV instance
        self._player = MPV()

        if self._audio_device:
            self._player["audio-device"] = self._audio_device

        # Set demuxer options for raw audio
        self._player["demuxer-rawaudio-rate"] = sample_rate
        self._player["demuxer-rawaudio-channels"] = channels
        self._player["demuxer-rawaudio-format"] = "s16le"
        self._player["demuxer"] = "rawaudio"
        self._player["cache"] = "no"

        # Set volume
        self._player.volume = (
            0
            if self._muted
            else (self._duck_volume if self._is_ducked else self._volume)
        )

        # Start playback from FIFO (in separate thread to not block)
        def start_playback():
            try:
                self._player.play(self._fifo_path)
            except Exception:
                _LOGGER.debug("MPV playback ended", exc_info=True)

        threading.Thread(target=start_playback, daemon=True).start()

        # Open FIFO for writing (this blocks until MPV opens it for reading)
        # Do this in a thread to avoid blocking the event loop
        def open_fifo():
            try:
                self._fifo_fd = os.open(self._fifo_path, os.O_WRONLY)
                _LOGGER.info(
                    "SendSpin audio stream started: %d Hz, %d channels",
                    sample_rate,
                    channels,
                )
            except Exception:
                _LOGGER.exception("Failed to open FIFO for writing")

        threading.Thread(target=open_fifo, daemon=True).start()

        self._current_format = fmt

    def _stop_player(self) -> None:
        """Stop the MPV player."""
        if self._player:
            try:
                self._player.stop()
                self._player.terminate()
            except Exception:
                _LOGGER.debug("Error stopping MPV player", exc_info=True)
            self._player = None

        self._cleanup_fifo()
        self._current_format = None

    def _stop_playback(self) -> None:
        """Stop playback and clear buffer."""
        # Cancel write task
        if self._write_task and not self._write_task.done():
            self._write_task.cancel()
        self._write_task = None

        # Clear buffer
        self._buffer.clear()

        # Stop player
        self._stop_player()

        # Reset state
        self._first_chunk_timestamp = None
        self._playback_started = False

    async def _connection_loop(self, url: str) -> None:
        """Connection loop with auto-reconnect."""
        if not self._client:
            return

        error_backoff = 1.0
        max_backoff = 300.0

        while self._running:
            try:
                await self._client.connect(url)
                error_backoff = 1.0

                # Wait for disconnect
                disconnect_event = asyncio.Event()
                unsubscribe = self._client.add_disconnect_listener(disconnect_event.set)
                await disconnect_event.wait()
                unsubscribe()

                _LOGGER.info("Disconnected from SendSpin server")

                if self._running:
                    _LOGGER.info("Reconnecting to %s", url)
            except Exception as e:
                _LOGGER.warning(
                    "SendSpin connection error (%s): %s, retrying in %.0fs",
                    type(e).__name__,
                    e,
                    error_backoff,
                )
                _LOGGER.debug("Full exception details:", exc_info=True)
                await asyncio.sleep(error_backoff)
                error_backoff = min(error_backoff * 2, max_backoff)

    def _on_stream_start(self, _message: StreamStartMessage) -> None:
        """Handle stream start from SendSpin server."""
        _LOGGER.info("SendSpin stream started - interrupting Home Assistant playback")
        self._stream_active = True
        self._playback_started = False
        self._first_chunk_timestamp = None
        self._buffer.clear()

        # Notify that SendSpin is starting (so HA music can stop)
        if self._on_sendspin_start:
            self._on_sendspin_start()

        # Stop any Home Assistant music that's currently playing
        if self.media_player and self.media_player.music_player.is_playing:
            self.media_player.music_player.stop()

        # Update MediaPlayerEntity state to show we're playing
        if self.media_player:
            self.media_player.server.send_messages(
                [self.media_player._update_state(MediaPlayerState.PLAYING)]
            )

    def _on_stream_end(self, roles) -> None:
        """Handle stream end from SendSpin server."""
        _LOGGER.info("SendSpin stream ended")
        self._stream_active = False

        # Stop playback
        self._stop_playback()

        # Update MediaPlayerEntity state to idle
        if self.media_player:
            self.media_player.server.send_messages(
                [self.media_player._update_state(MediaPlayerState.IDLE)]
            )

    def _on_server_command(self, payload: ServerCommandPayload) -> None:
        """Handle volume/mute commands from SendSpin server."""
        if payload.player is None or self._client is None:
            return

        player_cmd = payload.player

        if player_cmd.command == PlayerCommand.VOLUME and player_cmd.volume is not None:
            self._volume = player_cmd.volume
            if self._player:
                self._player.volume = 0 if self._muted else self._volume
            # Sync with MediaPlayerEntity
            if self.media_player:
                self.media_player.volume = self._volume / 100.0
                self.media_player.music_player.set_volume(self._volume)
                self.media_player.announce_player.set_volume(self._volume)
                self.media_player.server.send_messages(
                    [self.media_player._update_state(self.media_player.state)]
                )
            _LOGGER.info("SendSpin server set volume: %d%%", player_cmd.volume)

        elif player_cmd.command == PlayerCommand.MUTE and player_cmd.mute is not None:
            self._muted = player_cmd.mute
            if self._player:
                self._player.volume = 0 if self._muted else self._volume
            # Sync with MediaPlayerEntity
            if self.media_player:
                self.media_player.muted = self._muted
                self.media_player.server.send_messages(
                    [self.media_player._update_state(self.media_player.state)]
                )
            _LOGGER.info(
                "SendSpin server %s player", "muted" if player_cmd.mute else "unmuted"
            )

        # Send state update back to server per spec
        asyncio.get_event_loop().call_soon(
            lambda: asyncio.create_task(
                self._client.send_player_state(
                    state=PlayerStateType.SYNCHRONIZED,
                    volume=self._volume,
                    muted=self._muted,
                )
            )
        )

    def _on_audio_chunk(
        self,
        server_timestamp_us: int,
        audio_data: bytes,
        fmt: "AudioFormat",
    ) -> None:
        """Handle incoming audio chunk from SendSpin."""
        if not self._stream_active or not self._client:
            return

        # Store first chunk timestamp for sync calculation
        if self._first_chunk_timestamp is None:
            self._first_chunk_timestamp = server_timestamp_us

        # Start player if needed or if format changed
        if self._player is None or self._current_format != fmt:
            self._start_player(fmt)
            self._current_format = fmt

        # Buffer chunks before starting playback
        if not self._playback_started:
            self._buffer.append((server_timestamp_us, audio_data))

            if len(self._buffer) >= self._min_buffer_chunks:
                # Calculate when to start based on first buffered chunk
                first_ts = self._buffer[0][0]
                try:
                    target_play_time_us = self._client.compute_play_time(first_ts)
                    now_us = int(time.monotonic() * 1_000_000)
                    delay_us = target_play_time_us - now_us

                    # Start playback task with calculated delay
                    _LOGGER.info(
                        "Starting playback with %.2fs initial delay (buffered %d chunks)",
                        delay_us / 1_000_000.0,
                        len(self._buffer),
                    )
                    self._playback_started = True
                    self._write_task = asyncio.create_task(
                        self._continuous_write(delay_us / 1_000_000.0)
                    )
                except Exception as e:
                    _LOGGER.warning(
                        "Failed to calculate start time: %s, starting now", e
                    )
                    self._playback_started = True
                    self._write_task = asyncio.create_task(self._continuous_write(0.0))
        else:
            # Playback already started, just buffer for continuous writing
            self._buffer.append((server_timestamp_us, audio_data))

    async def _continuous_write(self, initial_delay: float) -> None:
        """Continuously write buffered chunks to FIFO."""
        try:
            # Wait for initial delay to sync with other clients
            if initial_delay > 0:
                await asyncio.sleep(min(initial_delay, 2.0))  # Cap at 2s

            # Write all buffered chunks first
            while self._buffer and self._fifo_fd is not None:
                _, audio_data = self._buffer.popleft()
                try:
                    await asyncio.get_event_loop().run_in_executor(
                        None, os.write, self._fifo_fd, audio_data
                    )
                except (BrokenPipeError, OSError) as e:
                    _LOGGER.warning("FIFO write error: %s", e)
                    return

            # Continue writing as new chunks arrive
            while self._stream_active and self._fifo_fd is not None:
                # Wait for new chunks with timeout
                for _ in range(50):  # Check 50 times (500ms total)
                    if self._buffer:
                        break
                    await asyncio.sleep(0.01)
                else:
                    # No chunks for 500ms, keep waiting
                    continue

                if self._buffer:
                    _, audio_data = self._buffer.popleft()
                    try:
                        await asyncio.get_event_loop().run_in_executor(
                            None, os.write, self._fifo_fd, audio_data
                        )
                    except (BrokenPipeError, OSError) as e:
                        _LOGGER.warning("FIFO write error: %s", e)
                        return

        except asyncio.CancelledError:
            _LOGGER.debug("Write task cancelled")
        except Exception:
            _LOGGER.exception("Error in continuous write loop")

    @property
    def connected(self) -> bool:
        """Check if connected to SendSpin server."""
        return self._client is not None and self._client.connected
