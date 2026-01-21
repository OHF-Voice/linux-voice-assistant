"""Configuration models for the application."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

_LOGGER = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Configuration Dataclasses
# -----------------------------------------------------------------------------

@dataclass
class AppConfig:
    """General application settings."""
    name: str
    wakeup_sound: str = "sounds/wake_word_triggered.flac"
    timer_finished_sound: str = "sounds/timer_finished.flac"
    preferences_file: str = "preferences.json"
    debug: bool = False


@dataclass
class AudioConfig:
    """Settings for audio input and output."""
    input_device: Optional[str] = None
    input_block_size: int = 1024
    output_device: Optional[str] = None

    # If True, LVA will attempt to set the OS sink volume to match the persisted
    # volume in preferences.json on startup (PipeWire/PulseAudio/ALSA best-effort).
    # Default is False to avoid surprising changes on systems where users manage
    # volume externally.
    volume_sync: bool = False

    # Maximum sink volume to map to LVA's 100% volume.
    #
    # Example: if max_volume_percent=150 then when LVA's media player reports
    # 100% (preferences.volume_level == 1.0), the underlying sink will be set
    # to 150% (or 1.5 for backends that use scalar volume).
    #
    # This is useful for devices that need >100% gain on PipeWire/Pulse/ALSA.
    max_volume_percent: int = 100


@dataclass
class WakeWordConfig:
    """Settings for wake word detection."""
    directories: List[str] = field(default_factory=list)
    model: str = "okay_nabu"
    stop_model: str = "stop"
    refractory_seconds: float = 2.0
    download_dir: str = "local"

    # OpenWakeWord activation threshold.
    # A wake word triggers when model probability exceeds this value.
    # Range: 0.0 - 1.0
    openwakeword_threshold: float = 0.5


@dataclass
class ESPHomeConfig:
    """Settings for the ESPHome API server."""
    host: str = "0.0.0.0"
    port: int = 6053


@dataclass
class LedConfig:
    """Settings for LEDs."""
    enabled: bool = True

    # Supported values include:
    # - "dotstar" / "neopixel" for Pi-attached LED strips
    # - "xvf3800" for the ReSpeaker XVF3800 USB LED ring backend
    led_type: str = "dotstar"

    # For dotstar/neopixel: "spi" or "gpio"
    # For xvf3800: "usb"
    interface: str = "spi"

    # GPIO pin numbers used when interface="gpio"
    clock_pin: int = 13
    data_pin: int = 12

    # Note: overridden by 'preferences.json' if it exists
    num_leds: int = 3


@dataclass
class MqttConfig:
    """Settings for the MQTT client."""
    enabled: bool = False
    host: Optional[str] = None
    port: int = 1883
    username: Optional[str] = None
    password: Optional[str] = None


@dataclass
class ButtonConfig:
    """Settings for a hardware momentary button."""

    # Overall enable/disable (default: off)
    enabled: bool = False

    # mode:
    #  - "gpio"   -> legacy GPIO button (e.g. ReSpeaker 2-Mic HAT)
    #  - "xvf3800"-> USB-based mute integration for the ReSpeaker XVF3800
    mode: str = "gpio"

    # BCM GPIO pin number for the button input (gpio mode only).
    pin: int = 17

    # Press duration (in seconds) to be considered a "long press" (gpio mode).
    long_press_seconds: float = 1.0

    # Poll interval used by both GPIO and XVF3800 controllers.
    poll_interval_seconds: float = 0.01


# -----------------------------------------------------------------------------
# Sendspin Configuration Dataclasses
# -----------------------------------------------------------------------------

@dataclass
class SendspinConnectionConfig:
    """
    Sendspin connection settings.

    mode:
      - "client_initiated": LVA discovers Sendspin servers via mDNS and connects.
      - "server_initiated": LVA advertises itself and accepts server connections.
    """
    mode: str = "client_initiated"
    mdns: bool = True
    server_host: Optional[str] = None
    server_port: int = 8927
    server_path: str = "/sendspin"


@dataclass
class SendspinRolesConfig:
    """Enable/disable Sendspin roles."""
    player: bool = True
    metadata: bool = True
    controller: bool = True
    artwork: bool = False
    visualizer: bool = False


@dataclass
class SendspinPlayerConfig:
    """Player capability preferences for Sendspin."""
    preferred_codec: str = "pcm"
    supported_codecs: List[str] = field(default_factory=lambda: ["pcm"])
    sample_rate: int = 48000
    channels: int = 2
    bit_depth: int = 16

    # Approximate stream buffer capacity to advertise (bytes).
    # This is used by the Sendspin server to choose chunk sizing.
    buffer_capacity_bytes: int = 1048576  # 1 MiB


@dataclass
class SendspinAudioOutputConfig:
    """Local audio output settings for Sendspin playback."""
    backend: str = "soundcard"  # Phase 1: soundcard experiment
    device: Optional[str] = None  # None -> default output device
    block_ms: int = 20
    prebuffer_ms: int = 300


@dataclass
class SendspinCoordinationConfig:
    """Coordination between voice interaction and Sendspin playback."""
    duck_during_voice: bool = True
    duck_gain: float = 0.3  # 0.0-1.0 multiplier applied to PCM samples
    on_error: str = "mute"  # "mute" | "stop"


@dataclass
class SendspinConfig:
    """Top-level Sendspin config block."""
    enabled: bool = False
    connection: SendspinConnectionConfig = field(default_factory=SendspinConnectionConfig)
    roles: SendspinRolesConfig = field(default_factory=SendspinRolesConfig)
    player: SendspinPlayerConfig = field(default_factory=SendspinPlayerConfig)
    audio_output: SendspinAudioOutputConfig = field(default_factory=SendspinAudioOutputConfig)
    coordination: SendspinCoordinationConfig = field(default_factory=SendspinCoordinationConfig)


@dataclass
class Config:
    """Main configuration object."""
    app: AppConfig
    audio: AudioConfig = field(default_factory=AudioConfig)
    wake_word: WakeWordConfig = field(default_factory=WakeWordConfig)
    esphome: ESPHomeConfig = field(default_factory=ESPHomeConfig)
    led: LedConfig = field(default_factory=LedConfig)
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    button: ButtonConfig = field(default_factory=ButtonConfig)
    sendspin: SendspinConfig = field(default_factory=SendspinConfig)

# -----------------------------------------------------------------------------
# Helper Function
# -----------------------------------------------------------------------------

def _clamp_0_1(name: str, value: float) -> float:
    """Clamp a float to [0.0, 1.0], logging a warning if clamped."""
    try:
        v = float(value)
    except Exception:
        _LOGGER.warning("%s is not a number (%r); using default 0.5", name, value)
        return 0.5

    if v < 0.0:
        _LOGGER.warning("%s < 0.0; clamping to 0.0 (was %s)", name, v)
        return 0.0
    if v > 1.0:
        _LOGGER.warning("%s > 1.0; clamping to 1.0 (was %s)", name, v)
        return 1.0
    return v


def load_config_from_json(config_path: Path) -> Config:
    """Loads configuration from a JSON file and populates dataclasses."""

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
    except FileNotFoundError:
        _LOGGER.critical("Configuration file not found at: %s", config_path)
        raise
    except json.JSONDecodeError as e:
        _LOGGER.critical("Error parsing configuration file: %s", e)
        raise

    if "app" not in raw_data:
        raise ValueError("Configuration file must contain an 'app' section with a 'name'.")

    app_config = AppConfig(**raw_data.get("app", {}))
    audio_config = AudioConfig(**raw_data.get("audio", {}))
    wake_word_config = WakeWordConfig(**raw_data.get("wake_word", {}))
    esphome_config = ESPHomeConfig(**raw_data.get("esphome", {}))
    led_config = LedConfig(**raw_data.get("led", {}))
    mqtt_config = MqttConfig(**raw_data.get("mqtt", {}))
    button_config = ButtonConfig(**raw_data.get("button", {}))

    # --- Sendspin (nested dataclasses; keep robust to partial configs) ---
    sendspin_raw = raw_data.get("sendspin", {}) if isinstance(raw_data.get("sendspin", {}), dict) else {}
    sendspin_cfg = SendspinConfig(
        enabled=bool(sendspin_raw.get("enabled", False)),
        connection=SendspinConnectionConfig(**(sendspin_raw.get("connection", {}) or {})),
        roles=SendspinRolesConfig(**(sendspin_raw.get("roles", {}) or {})),
        player=SendspinPlayerConfig(**(sendspin_raw.get("player", {}) or {})),
        audio_output=SendspinAudioOutputConfig(**(sendspin_raw.get("audio_output", {}) or {})),
        coordination=SendspinCoordinationConfig(**(sendspin_raw.get("coordination", {}) or {})),
    )

    # Normalize / validate wake word threshold
    wake_word_config.openwakeword_threshold = _clamp_0_1(
        "wake_word.openwakeword_threshold",
        getattr(wake_word_config, "openwakeword_threshold", 0.5),
    )

    # Normalize / validate Sendspin duck_gain
    sendspin_cfg.coordination.duck_gain = _clamp_0_1(
        "sendspin.coordination.duck_gain",
        getattr(sendspin_cfg.coordination, "duck_gain", 0.3),
    )

    # Back-compat: allow top-level "volume_sync" (preferred location is audio.volume_sync)
    if "volume_sync" in raw_data and "volume_sync" not in raw_data.get("audio", {}):
        try:
            audio_config.volume_sync = bool(raw_data.get("volume_sync"))
        except Exception:
            # If it's something strange, just leave default.
            pass

    # Set MQTT 'enabled' flag
    if mqtt_config.host:
        mqtt_config.enabled = True

    # Validate configuration
    _validate_config(app_config, audio_config, wake_word_config, esphome_config)

    return Config(
        app=app_config,
        audio=audio_config,
        wake_word=wake_word_config,
        esphome=esphome_config,
        led=led_config,
        mqtt=mqtt_config,
        button=button_config,
        sendspin=sendspin_cfg,
    )


def _validate_config(
    app: AppConfig,
    audio: AudioConfig,
    wake_word: WakeWordConfig,
    esphome: ESPHomeConfig,
) -> None:
    """
    Validate configuration for common issues and log warnings.

    Does not raise exceptions, only logs warnings for potential problems.
    """
    # Validate audio block size
    if audio.input_block_size < 256:
        _LOGGER.warning(
            "audio.input_block_size (%d) is very small. Recommended: 512-2048",
            audio.input_block_size
        )
    elif audio.input_block_size > 4096:
        _LOGGER.warning(
            "audio.input_block_size (%d) is very large. May cause latency. Recommended: 512-2048",
            audio.input_block_size
        )

    # Validate refractory period
    if wake_word.refractory_seconds < 0:
        _LOGGER.warning(
            "wake_word.refractory_seconds is negative (%f). Setting to 0.",
            wake_word.refractory_seconds
        )
        wake_word.refractory_seconds = 0.0
    elif wake_word.refractory_seconds > 10.0:
        _LOGGER.warning(
            "wake_word.refractory_seconds (%f) is very high. Users may experience delays.",
            wake_word.refractory_seconds
        )

    # Validate OpenWakeWord threshold (already clamped, but warn if extreme)
    if wake_word.openwakeword_threshold < 0.3:
        _LOGGER.warning(
            "wake_word.openwakeword_threshold (%f) is low. May cause false activations.",
            wake_word.openwakeword_threshold
        )
    elif wake_word.openwakeword_threshold > 0.7:
        _LOGGER.warning(
            "wake_word.openwakeword_threshold (%f) is high. Wake word may be hard to trigger.",
            wake_word.openwakeword_threshold
        )

    # Validate ESPHome port
    if esphome.port < 1024:
        _LOGGER.warning(
            "esphome.port (%d) is below 1024. May require root privileges on some systems.",
            esphome.port
        )
    elif esphome.port > 65535:
        _LOGGER.error(
            "esphome.port (%d) is invalid (must be 1-65535). Using default 6053.",
            esphome.port
        )
        esphome.port = 6053

    # Validate sound files exist
    from pathlib import Path
    for sound_name, sound_path in [
        ("wakeup_sound", app.wakeup_sound),
        ("timer_finished_sound", app.timer_finished_sound),
    ]:
        if not Path(sound_path).exists():
            _LOGGER.warning(
                "%s file not found: %s. Audio playback will fail.",
                sound_name,
                sound_path
            )
