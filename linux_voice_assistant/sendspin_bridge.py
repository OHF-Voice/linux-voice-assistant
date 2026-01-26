"""Bridge between SendSpin client and MediaPlayer entity."""

import asyncio
import logging
import socket
import subprocess
from typing import TYPE_CHECKING, Optional

from aioesphomeapi.model import MediaPlayerState
from aiosendspin.client import SendspinClient
from aiosendspin.models.core import DeviceInfo, StreamStartMessage
from aiosendspin.models.player import ClientHelloPlayerSupport, SupportedAudioFormat
from aiosendspin.models.types import AudioCodec, PlayerCommand, Roles

if TYPE_CHECKING:
    from aiosendspin.client import AudioFormat

    from .entity import MediaPlayerEntity

_LOGGER = logging.getLogger(__name__)


class SendspinBridge:
    """Bridge that connects SendSpin streaming to MediaPlayerEntity.

    When SendSpin server starts streaming, this bridge plays the audio using
    MPV (same as the rest of LVA) and updates the MediaPlayerEntity state to
    reflect that SendSpin (not Home Assistant) is the active source.
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
        self._mpv_process: Optional[subprocess.Popen] = None
        self._current_format: Optional["AudioFormat"] = None

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

    async def start(self, server_url: Optional[str] = None) -> None:
        """Start the SendSpin client.

        Args:
            server_url: Optional server URL to connect to
        """
        if self._running or not self._client:
            return

        self._running = True
        _LOGGER.info("Starting SendSpin bridge: %s", self.client_id)

        if server_url:
            # Connect to specific server
            asyncio.create_task(self._connection_loop(server_url))
        else:
            _LOGGER.info(
                "SendSpin bridge started (no server URL - waiting for connections)"
            )

    async def stop(self) -> None:
        """Stop the SendSpin client."""
        if not self._running:
            return

        _LOGGER.info("Stopping SendSpin bridge")
        self._running = False

        self._stop_mpv()

        if self._client and self._client.connected:
            await self._client.disconnect()

    def _stop_mpv(self) -> None:
        """Stop the MPV process if running."""
        if self._mpv_process:
            try:
                self._mpv_process.stdin.close()
                self._mpv_process.terminate()
                self._mpv_process.wait(timeout=2)
            except Exception:
                _LOGGER.debug("Error stopping MPV process", exc_info=True)
            self._mpv_process = None
            self._current_format = None

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

        # Stop MPV process
        self._stop_mpv()

        # Update MediaPlayerEntity state to idle
        if self.media_player:
            self.media_player.server.send_messages(
                [self.media_player._update_state(MediaPlayerState.IDLE)]
            )

    def _on_audio_chunk(
        self,
        server_timestamp_us: int,
        audio_data: bytes,
        fmt: "AudioFormat",
    ) -> None:
        """Handle incoming audio chunk from SendSpin."""
        if not self._stream_active:
            return

        # Start MPV process if needed or if format changed
        if self._mpv_process is None or self._current_format != fmt:
            self._start_mpv(fmt)

        # Write audio data to MPV's stdin
        if self._mpv_process and self._mpv_process.stdin:
            try:
                self._mpv_process.stdin.write(audio_data)
                self._mpv_process.stdin.flush()
            except (BrokenPipeError, OSError):
                _LOGGER.warning("MPV pipe broken, restarting")
                self._stop_mpv()

    def _start_mpv(self, fmt: "AudioFormat") -> None:
        """Start MPV process for raw PCM playback."""
        self._stop_mpv()

        pcm_format = fmt.pcm_format
        sample_rate = pcm_format.sample_rate
        channels = pcm_format.channels

        # Build MPV command for raw PCM input
        # Format: s16le (signed 16-bit little-endian)
        cmd = [
            "mpv",
            "--no-terminal",
            "--no-video",
            f"--demuxer-rawaudio-rate={sample_rate}",
            f"--demuxer-rawaudio-channels={channels}",
            "--demuxer-rawaudio-format=s16le",
            "--demuxer=rawaudio",
            "--cache=no",
            "--stream-buffer-size=64k",
        ]

        # Use same audio device as the rest of LVA
        if self._audio_device:
            cmd.append(f"--audio-device={self._audio_device}")

        # Read from stdin
        cmd.append("-")

        try:
            self._mpv_process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._current_format = fmt
            _LOGGER.info(
                "SendSpin audio stream started via MPV: %d Hz, %d channels",
                sample_rate,
                channels,
            )
        except Exception:
            _LOGGER.exception("Failed to start MPV for SendSpin audio")

    @property
    def connected(self) -> bool:
        """Check if connected to SendSpin server."""
        return self._client is not None and self._client.connected

    @property
    def is_running(self) -> bool:
        """Check if bridge is running."""
        return self._running
