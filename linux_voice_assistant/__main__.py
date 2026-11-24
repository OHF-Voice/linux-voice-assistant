#!/usr/bin/env python3
import argparse
import asyncio
import errno
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, List, Optional

import numpy as np
import soundcard as sc
from aioesphomeapi.api_pb2 import NumberStateResponse  # type: ignore  # pylint: disable=no-name-in-module
from pymicro_wakeword import MicroWakeWordFeatures
from pyopen_wakeword import OpenWakeWord, OpenWakeWordFeatures

from .api_server import create_encryption_key_file, load_encryption_key_file
from .models import Preferences, ServerState, initial_stop_word_threshold
from .mpv_player import MpvMediaPlayer
from .peripheral_api import LVAEvent, PeripheralAPIServer
from .satellite import VoiceSatelliteProtocol
from .stop_word import process_stop_word
from .util import (
    get_default_interface,
    get_default_ipv4,
    get_esphome_version,
    get_version,
    resolve_mac_address,
    warn_about_removed_options,
)
from .wake_word import find_available_wake_words, load_stop_model, load_wake_models
from .wake_word_detection import ActiveWakeWord, apply_sensitivities, gate_wake_words, process_wake_words, select_active_wake_words
from .webrtc import WebRTCProcessor
from .zeroconf import start_discovery

_LOGGER = logging.getLogger(__name__)

_MIC_RETRY_DELAY_SECONDS = 2.0
_LOG_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)s:%(name)s:%(message)s"
_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_MODULE_DIR = Path(__file__).parent
_REPO_DIR = _MODULE_DIR.parent
_WAKEWORDS_DIR = _REPO_DIR / "wakewords"
_SOUNDS_DIR = _REPO_DIR / "sounds"


# -----------------------------------------------------------------------------


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--name",
        help="Real name for the device",
    )
    parser.add_argument(
        "--audio-input-device",
        help="Name for the audio input device (see --list-input-devices)",
    )
    parser.add_argument(
        "--list-input-devices",
        action="store_true",
        help="List audio input devices and exit",
    )
    parser.add_argument(
        "--audio-input-block-size",
        type=int,
        default=1024,
    )
    parser.add_argument(
        "--audio-output-device",
        default="pulse",
        help="Name for the audio output device, use a pulse/... name (see --list-output-devices)",
    )
    parser.add_argument(
        "--music-output-device",
        help="mpv name for the music/media output device (defaults to --audio-output-device)",
    )
    parser.add_argument(
        "--list-output-devices",
        action="store_true",
        help="List audio output devices and exit",
    )
    parser.add_argument(
        "--mic-volume",
        type=int,
        default=None,
        choices=list(range(101)),
        help="Microphone volume level (0 to 100), overrides the stored value on every start. If not given, the stored value is used, or 100 if none is stored",
    )
    parser.add_argument("--mic-auto-gain", type=int, default=0, choices=list(range(32)))
    parser.add_argument("--mic-noise-suppression", type=int, default=0, choices=(0, 1, 2, 3, 4))
    parser.add_argument(
        "--audio-input-channels",
        type=int,
        default=1,
        choices=(1, 2),
        help="Number of mic channels to capture (1=mono, 2=mic + AEC reference channel); what is streamed to Home Assistant does not depend on it",
    )
    parser.add_argument(
        "--wake-word-root-dir",
        default=_WAKEWORDS_DIR,
        help="Root directory of the wake word layout: <origin>/<engine> with origin internal, downloaded or custom and engine microwakeword or openwakeword",
    )
    parser.add_argument(
        "--wake-word-dir",
        default=None,
        help="Removed and ignored, use --wake-word-root-dir",
    )
    parser.add_argument(
        "--download-dir",
        default=None,
        help="Removed and ignored, downloaded wake words are stored in <--wake-word-root-dir>/downloaded",
    )
    parser.add_argument(
        "--wake-model",
        default="okay_nabu",
        help="File name of the first active wake model",
    )
    parser.add_argument(
        "--wake-model-2",
        default="",
        help="File name of the second active wake model, used only when no wake words are stored yet (empty = no second wake word)",
    )
    parser.add_argument(
        "--stop-model",
        default="stop",
        help="File name of the stop model",
    )
    parser.add_argument(
        "--refractory-seconds",
        default=0.0,
        type=float,
        help="Seconds after an accepted wake word during which further detections are blocked (0 = off)",
    )
    parser.add_argument(
        "--continue-conversation-delay",
        type=float,
        default=0.5,
        help="Seconds to wait after TTS finishes before opening the mic for continued conversation (default: 0.5)",
    )
    parser.add_argument(
        "--wakeup-sound",
        default=str(_SOUNDS_DIR / "wake_word_triggered.flac"),
        help="Directory and file name for wake sound (when you say the wake word)",
    )
    parser.add_argument(
        "--start-listening-sound",
        default=str(_SOUNDS_DIR / "start_listening_button.flac"),
        help="Directory and file name and sound for start listening button (when you press button to talk)",
    )
    parser.add_argument(
        "--timer-finished-sound",
        default=str(_SOUNDS_DIR / "timer_finished.flac"),
        help="Directory and file name for timer finished sound",
    )
    parser.add_argument(
        "--processing-sound",
        default=str(_SOUNDS_DIR / "processing.wav"),
        help="Short sound to play while assistant is processing (thinking)",
    )
    parser.add_argument(
        "--mute-sound",
        default=str(_SOUNDS_DIR / "mute_switch_on.flac"),
        help="Sound to play when muting the assistant",
    )
    parser.add_argument(
        "--unmute-sound",
        default=str(_SOUNDS_DIR / "mute_switch_off.flac"),
        help="Sound to play when unmuting the assistant",
    )
    parser.add_argument(
        "--pipeline-ended-sound",
        default=str(_SOUNDS_DIR / "pipeline_ended_low.flac"),
        help="Sound to play when the voice pipeline ends without a response (e.g. no speech detected)",
    )
    parser.add_argument(
        "--button-double-press-sound",
        default=str(_SOUNDS_DIR / "button_double_press.flac"),
        help="Sound to play for button double press",
    )
    parser.add_argument(
        "--button-triple-press-sound",
        default=str(_SOUNDS_DIR / "button_triple_press.flac"),
        help="Sound to play for button triple press",
    )
    parser.add_argument(
        "--button-long-press-sound",
        default=str(_SOUNDS_DIR / "button_long_press.flac"),
        help="Sound to play for button long press",
    )
    parser.add_argument(
        "--preferences-file",
        default=_REPO_DIR / "preferences.json",
        help="Directory and file name for the preferences JSON file",
    )
    parser.add_argument(
        "--host",
        help="Optional host IP address to bind to (default: auto-detected by network interface)",
    )
    parser.add_argument(
        "--network-interface",
        help="Network interface the application listens on (default: auto-detected by gateway)",
    )
    parser.add_argument(
        "--mac-address",
        help="MAC address the device is known by in Home Assistant, e.g. 02:00:00:00:00:01 "
        "(default: the network interface's). Set it to keep one identity across interfaces, "
        "or to run several instances on one machine",
    )
    # Note that default port is also set in docker-entrypoint.sh
    parser.add_argument(
        "--port",
        type=int,
        default=6053,
        help="Port the application is listening on (default: 6053)",
    )
    parser.add_argument(
        "--enable-encryption",
        action="store_true",
        help="Encrypt the ESPHome API with the key from --encryption-key-file; Home Assistant then needs the same key, clients without it are refused",
    )
    parser.add_argument(
        "--encryption-key-file",
        help="File holding the encryption key (32 bytes, base64; for example from: openssl rand -base64 32). Default: encryption.key next to the preferences file. Created with a new random key if it does not exist",
    )
    parser.add_argument(
        "--enable-thinking-sound",
        action="store_true",
        help="Enable thinking sound on startup",
    )
    # ------------------------------------------------------------------
    # Peripheral API (LEDs, buttons, HAT boards)
    # ------------------------------------------------------------------
    parser.add_argument(
        "--peripheral-host",
        default="0.0.0.0",
        help="Bind address for the peripheral WebSocket API (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--peripheral-port",
        type=int,
        default=6055,
        help="Port for the peripheral WebSocket API (default: 6055)",
    )
    parser.add_argument(
        "--peripheral-volume-step",
        type=float,
        default=PeripheralAPIServer.DEFAULT_VOLUME_STEP,
        metavar="STEP",
        help="Volume change per button press, 0.0–1.0 (default: %(default)s)",
    )
    parser.add_argument(
        "--disable-peripheral-api",
        action="store_true",
        help="Disable the peripheral WebSocket API entirely",
    )
    parser.add_argument(
        "--enable-zeroconf",
        type=int,
        choices=(0, 1),
        default=1,
        help="Announce LVA via mDNS (default: %(default)s). Set 0 to not announce it: Home Assistant then no longer discovers it automatically, add it manually with the host IP address and --port.",
    )
    parser.add_argument(
        "--peripheral-startup-wait",
        type=float,
        default=2.0,
        metavar="SECONDS",
        help="Seconds to wait for peripherals to connect and register their entities before HA enumerates the ESPHome API (default: %(default)s; set 0 to skip).",
    )
    # ------------------------------------------------------------------
    parser.add_argument(
        "--timer-max-ring-seconds",
        type=float,
        default=900.0,  # 15 minutes
        help="Seconds before a ringing timer auto-stops (default: 900)",
    )
    parser.add_argument(
        "--listen-during-wake-sound",
        action="store_true",
        help="Start listening immediately after wake word detection, without waiting for the wake sound to finish",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Add this to enable debug logging",
    )
    parser.add_argument(
        "--colored-debug",
        action="store_true",
        help="Add this to enable colored debug logging",
    )
    parser.add_argument(
        "--output-only",
        action="store_true",
        help="Enable output only mode",
    )
    args = parser.parse_args()

    if args.colored_debug:
        args.debug = True
        _setup_logging(args)
    elif args.debug:
        logging.basicConfig(level=logging.DEBUG, format=_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT)
    else:
        logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT)

    _LOGGER.debug(args)
    if args.list_input_devices:
        print("Audio Input devices:")
        print("=" * 13)
        for idx, mic in enumerate(sc.all_microphones()):
            print(f"[{idx}]", mic.name)
        return

    if args.list_output_devices:
        from mpv import MPV

        player = MPV()
        print("Audio output devices:")
        print("=" * 14)

        for speaker in player.audio_device_list:  # type: ignore
            print(speaker["name"] + ":", speaker["description"])
        return

    # Resolve network interface for mac-address detection
    if not args.network_interface:
        _LOGGER.info("No network interface specified, try to detect default interface")
        network_interface = get_default_interface()
        _LOGGER.info("Default interface detected: %s", network_interface)
    else:
        _LOGGER.info("Network interface specified")
        network_interface = args.network_interface
        _LOGGER.info("Using network interface: %s", network_interface)

    # Resolve ip_address where the application will be listening
    if not args.host:
        _LOGGER.info("No host (ip-address) specified, try to detect IP-Address")
        host_ip_address = get_default_ipv4(network_interface)
        _LOGGER.info("IP-Address detected: %s", host_ip_address)
    else:
        _LOGGER.info("Custom Host specified")
        _LOGGER.info("Using host: %s", args.host)
        host_ip_address = args.host

    # Resolve mac
    try:
        mac_address = resolve_mac_address(args.mac_address, network_interface)
    except ValueError as err:
        _LOGGER.error("%s, app stopped.", err)
        sys.exit(1)
    if args.mac_address:
        _LOGGER.info("Using custom MAC address: %s", mac_address)
    mac_address_clean = mac_address.replace(":", "").lower()

    # Resolve name
    if not args.name:
        _LOGGER.info("No friendly name specified, try to autogenerate name")
        friendly_name = f"LVA - {mac_address_clean}"
        _LOGGER.info("Friendly name autogenerated: %s", friendly_name)
    else:
        _LOGGER.info("Friendly name specified")
        _LOGGER.info("Using custom friendly name: %s", args.name)
        friendly_name = args.name

    device_name = f"lva-{mac_address_clean}"

    # Resolve encryption key
    noise_psk: Optional[bytes] = None
    if args.enable_encryption:
        key_path = Path(args.encryption_key_file) if args.encryption_key_file else Path(args.preferences_file).parent / "encryption.key"
        try:
            if not key_path.exists():
                create_encryption_key_file(key_path)
                # Only the path is logged, never the key: logs are less protected than the key file
                _LOGGER.warning("Created encryption key file: %s. Home Assistant will ask for this key, read it from that file.", key_path)
            noise_psk = load_encryption_key_file(key_path)
        except OSError as err:
            _LOGGER.error("Cannot read the encryption key file: %s, app stopped.", err)
            sys.exit(1)
        except ValueError as err:
            _LOGGER.error("%s (%s), app stopped.", err, key_path)
            sys.exit(1)
        _LOGGER.info("API encryption: enabled (key file %s)", key_path)
    else:
        _LOGGER.info("API encryption: disabled (plaintext)")

    _LOGGER.info("Device name: %s", device_name)

    # Resolve version
    version = get_version()
    _LOGGER.info("Version: %s", version)
    warn_about_removed_options(args.wake_word_dir, args.download_dir)

    # Resolve esphome version
    esphome_version = get_esphome_version()
    _LOGGER.info("ESPHome api version: %s", esphome_version)

    # Resolve microphone
    if args.audio_input_device is not None:
        try:
            args.audio_input_device = int(args.audio_input_device)
        except ValueError:
            pass

        try:
            mic = sc.get_microphone(args.audio_input_device)
        except IndexError:
            _LOGGER.error("Audio input device %r not found.", args.audio_input_device)
            _LOGGER.error("Available input devices:")
            for idx, available_mic in enumerate(sc.all_microphones()):
                _LOGGER.error("  [%d] %s", idx, available_mic.name)
            sys.exit(1)
    else:
        mic = sc.default_microphone()

    # Validate output devices, mpv silently accepts unknown names
    for output_device in (args.audio_output_device, args.music_output_device):
        if output_device and not _output_device_exists(output_device):
            _LOGGER.error("Audio output device %r not found.", output_device)
            _LOGGER.error("Available output devices:")
            for idx, speaker in enumerate(_list_output_devices()):
                _LOGGER.error("  [%d] %s: %s", idx, speaker["name"], speaker["description"])
            sys.exit(1)

    # Load available wake words
    wake_word_root = Path(args.wake_word_root_dir)
    # Lowest priority first: a wake word id found in a later directory replaces the same id found earlier
    wake_word_dirs = [wake_word_root / origin / engine for origin in ("internal", "downloaded", "custom") for engine in ("microwakeword", "openwakeword")]
    for wake_word_dir in wake_word_dirs:
        wake_word_dir.mkdir(parents=True, exist_ok=True)

    available_wake_words = find_available_wake_words(wake_word_dirs, args.stop_model)

    # Load preferences
    preferences_path = Path(args.preferences_file)
    if preferences_path.exists():
        _LOGGER.debug("Loading preferences: %s", preferences_path)
        with open(preferences_path, "r", encoding="utf-8") as preferences_file:
            preferences_dict = json.load(preferences_file)
            preferences = Preferences.from_dict(preferences_dict)
    else:
        _LOGGER.info("No preferences file found, creating it with the defaults: %s", preferences_path)
        preferences = Preferences()
        preferences.save(preferences_path)

    # Load volume from preferences on startup, and ensure it's between 0.0 and 1.0
    initial_volume = preferences.volume if preferences.volume is not None else 1.0
    initial_volume = max(0.0, min(1.0, float(initial_volume)))
    preferences.volume = initial_volume

    # Load stop word sensitivity from preferences on startup, and ensure it's between 0.0 and 1.0
    initial_threshold = initial_stop_word_threshold(preferences.stop_word_sensitivity)
    preferences.stop_word_sensitivity = initial_threshold

    # Load button-lock state from preferences on startup. This must happen
    # before ServerState is constructed (and therefore before the peripheral
    # WebSocket server can accept any connection), because the very first
    # snapshot sent to a (re)connecting peripheral must already reflect the
    # persisted value — peripherals only learn the lock state from that
    # snapshot or a later button_lock_changed event, and nothing re-sends
    # a corrected snapshot after the fact.
    initial_button_controls_locked = bool(preferences.button_controls_locked)

    if args.enable_thinking_sound:
        preferences.thinking_sound = 1

    if args.mic_auto_gain or args.mic_noise_suppression:
        try:
            import webrtc_noise_gain  # type: ignore[import-untyped] # noqa: F401
        except ImportError:
            _LOGGER.exception("Extras for webrtc are not installed")
            sys.exit(1)

    if args.mic_volume is not None:
        preferences.mic_volume = args.mic_volume
    if args.mic_auto_gain > 0:
        preferences.mic_auto_gain = args.mic_auto_gain

    if args.mic_noise_suppression > 0:
        preferences.mic_noise_suppression = args.mic_noise_suppression

    # Load wake/stop models
    wake_models, wake_word_slots, fallback_used = load_wake_models(
        available_wake_words,
        [word for word in preferences.active_wake_words if word is not None],
        first_default_wake_word_id=args.wake_model,
        second_default_wake_word_id=args.wake_model_2,
    )

    # TODO: allow openWakeWord for "stop"
    stop_model = load_stop_model(list(reversed(wake_word_dirs)), args.stop_model)
    assert stop_model is not None

    state = ServerState(
        name=device_name,
        friendly_name=friendly_name,
        network_interface=network_interface,
        mac_address=mac_address,
        ip_address=host_ip_address,
        version=version,
        esphome_version=esphome_version,
        entities=[],
        available_wake_words=available_wake_words,
        wake_words=wake_models,
        active_wake_words=set(wake_word_slots),
        stop_word=stop_model,
        music_player=MpvMediaPlayer(device=args.music_output_device or args.audio_output_device),
        tts_player=MpvMediaPlayer(device=args.audio_output_device),
        wakeup_sound=args.wakeup_sound,
        start_listening_sound=args.start_listening_sound,
        timer_finished_sound=args.timer_finished_sound,
        processing_sound=args.processing_sound,
        mute_sound=args.mute_sound,
        noise_psk=noise_psk,
        unmute_sound=args.unmute_sound,
        pipeline_ended_sound=args.pipeline_ended_sound,
        button_double_press_sound=args.button_double_press_sound,
        button_triple_press_sound=args.button_triple_press_sound,
        button_long_press_sound=args.button_long_press_sound,
        preferences=preferences,
        preferences_path=preferences_path,
        refractory_seconds=args.refractory_seconds,
        continue_conversation_delay=args.continue_conversation_delay,
        output_only=args.output_only,
        wake_word_root=wake_word_root,
        volume=initial_volume,
        stop_word_threshold=initial_threshold,
        button_controls_locked=initial_button_controls_locked,
        mic_volume=preferences.mic_volume,
        mic_auto_gain=preferences.mic_auto_gain,
        mic_noise_suppression=preferences.mic_noise_suppression,
        audio_input_channels=args.audio_input_channels,
        timer_max_ring_seconds=args.timer_max_ring_seconds,
        listen_during_wake_sound=args.listen_during_wake_sound,
    )

    if fallback_used:
        # Fallback to the default model was used, save as active wake words
        _LOGGER.debug("Fallback was used, save default wake words in Preferences.")
        state.preferences.active_wake_words = list(wake_word_slots)
        state.active_wake_words = set(wake_word_slots)
        state.wake_words = wake_models
        state.preferences.save(state.preferences_path)
        state.wake_words_changed = True

    if args.enable_thinking_sound or args.mic_volume is not None or args.mic_auto_gain or args.mic_noise_suppression:
        state.preferences.save(state.preferences_path)

    initial_volume_percent = int(round(initial_volume * 100))
    state.music_player.set_volume(initial_volume_percent)
    state.tts_player.set_volume(initial_volume_percent)

    # ------------------------------------------------------------------
    # Peripheral API (optional – LEDs, buttons, HAT boards)
    # ------------------------------------------------------------------
    peripheral_api: Optional[PeripheralAPIServer] = None
    if not args.disable_peripheral_api:
        peripheral_api = PeripheralAPIServer(
            host=args.peripheral_host,
            port=args.peripheral_port,
            volume_step=args.peripheral_volume_step,
        )
        peripheral_api.set_state(state)
        state.peripheral_api = peripheral_api

    # ------------------------------------------------------------------
    # ESPHome TCP server (with retry on EADDRINUSE)
    # ------------------------------------------------------------------
    loop = asyncio.get_running_loop()
    max_attempts = 15
    attempt = 1
    server = None

    # Validate VoiceSatelliteProtocol initialization BEFORE starting server
    # This catches errors like missing imports or broken initialization immediately
    # instead of failing silently only when first client connects
    _LOGGER.debug("Validating VoiceSatelliteProtocol initialization...")
    try:
        # Create test instance to run complete __init__ code path
        test_protocol = VoiceSatelliteProtocol(state)
        # Cleanup state reference
        test_protocol.state.satellite = None
        del test_protocol
        _LOGGER.debug("✅ VoiceSatelliteProtocol validation successful")
    except Exception:
        _LOGGER.critical("❌ FATAL ERROR in VoiceSatelliteProtocol initialization!", exc_info=True)
        _LOGGER.critical("Program will exit immediately - fix the error above first!")
        sys.exit(1)

    while attempt <= max_attempts:
        try:
            server = await loop.create_server(
                lambda: VoiceSatelliteProtocol(state),
                host=host_ip_address,
                port=args.port,
            )
            break  # connection successful, exit the loop
        except OSError as err:
            message = err.strerror or str(err)
            if err.errno == errno.EADDRINUSE:
                message = "address already in use"
            if attempt < max_attempts:
                _LOGGER.warning(
                    "Attempt %d/%d failed to bind on address (%s, %s): %s. Retrying in 1 second...",
                    attempt,
                    max_attempts,
                    host_ip_address,
                    args.port,
                    message,
                )
                await asyncio.sleep(1)
                attempt += 1
            else:
                _LOGGER.exception(
                    "All %d attempts failed to bind on address (%s, %s): %s",
                    max_attempts,
                    host_ip_address,
                    args.port,
                    message,
                )
                sys.exit(1)

    # ------------------------------------------------------------------
    # Audio processing thread
    # ------------------------------------------------------------------
    process_audio_thread = threading.Thread(
        target=process_audio,
        args=(state, mic, args.audio_input_block_size),
        daemon=True,
    )
    process_audio_thread.start()

    # Auto discovery (zeroconf, mDNS)
    await start_discovery(
        enabled=args.enable_zeroconf == 1,
        port=args.port,
        name=state.name,
        mac_address=state.mac_address,
        host_ip_address=host_ip_address,
        encrypted=noise_psk is not None,
    )

    # ------------------------------------------------------------------
    # Start peripheral API and signal "getting started" to peripherals
    # ------------------------------------------------------------------
    if peripheral_api is not None:
        await peripheral_api.start()
        await peripheral_api.emit_event(LVAEvent.ZEROCONF, {"status": "getting_started"})

        # Give peripherals a window to connect and register their Light
        # entities before HA enumerates over the ESPHome native API. The
        # ESPHome server is bound but not yet serving (serve_forever runs
        # below), so any HA connection sits queued in the kernel for the
        # duration of this wait. Peripherals that register later still
        # work, but the new entities only show up in HA after the
        # integration reconnects.
        if args.peripheral_startup_wait > 0:
            _LOGGER.info(
                "Waiting %.1fs for peripherals to register entities…",
                args.peripheral_startup_wait,
            )
            await asyncio.sleep(args.peripheral_startup_wait)

    try:
        async with server:  # type: ignore[union-attr]
            _LOGGER.info("Server started (host=%s, port=%s)", host_ip_address, args.port)
            await server.serve_forever()  # type: ignore[union-attr]
    except KeyboardInterrupt:
        pass
    finally:
        process_audio_thread.join()
        if peripheral_api is not None:
            await peripheral_api.stop()

    _LOGGER.debug("Server stopped")


# -----------------------------------------------------------------------------
def _list_output_devices() -> list:
    """Return the audio output devices reported by mpv."""
    from mpv import MPV

    return list(MPV().audio_device_list)  # type: ignore


def _output_device_exists(device: str) -> bool:
    """
    Check whether mpv knows the given output device.
    :param device: Device name as passed to --audio-output-device, a bare driver name like "pulse" matches any "pulse/..." device.
    """
    names = [speaker["name"] for speaker in _list_output_devices()]
    return device in names or any(name.startswith(f"{device}/") for name in names)


def _setup_logging(args: argparse.Namespace) -> None:
    COLORS = {
        logging.DEBUG: "\033[36m",
        logging.INFO: "\033[32m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[35m",
    }
    RESET = "\033[0m"

    original_format = logging.Formatter.format

    def colored_format(self, record: logging.LogRecord) -> str:
        color = COLORS.get(record.levelno, RESET)
        return f"{color}{original_format(self, record)}{RESET}"

    logging.Formatter.format = colored_format  # type: ignore

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        handlers=[handler],
    )


# -----------------------------------------------------------------------------


def process_audio(state: ServerState, mic, block_size: int):
    """Process audio chunks from the microphone."""
    n_channels = state.audio_input_channels

    wake_words: List[ActiveWakeWord] = []
    micro_features: Optional[MicroWakeWordFeatures] = None
    micro_inputs: List[np.ndarray] = []

    oww_features: Optional[OpenWakeWordFeatures] = None
    oww_inputs: List[np.ndarray] = []
    has_oww = False

    last_active: Optional[float] = None
    webrtc: Optional[WebRTCProcessor] = None
    stop_word_running = False
    wake_word_running = False

    try:
        _LOGGER.debug("Opening audio input device: %s", mic.name)
        with _ReconnectingRecorder(mic, n_channels, block_size) as mic_in:
            while True:
                # Shape: (block_size, n_channels) for stereo, (block_size, 1) for mono.
                raw = mic_in.record(block_size)  # float32, range [-1, 1]
                mic_vol_scalar = max(0.1, min(1.0, state.mic_volume / 100.0))

                # Build per-channel byte arrays.  Channel 0 is the primary
                # microphone; channel 1 (when present) is the reference/speaker
                # feed used for server-side AEC.
                channel_chunks: list[bytes] = []
                for ch in range(n_channels):
                    col = raw[:, ch] if n_channels > 1 else raw.reshape(-1)
                    channel_chunks.append(_float_to_pcm16(col, mic_vol_scalar))

                # Primary channel drives WebRTC and wake-word detection.
                audio_chunk = channel_chunks[0]
                # Save raw before any processing
                audio_chunk_unprocessed = channel_chunks[0]
                agc = state.preferences.mic_auto_gain or 0
                ns = state.preferences.mic_noise_suppression or 0

                if agc > 0 or ns > 0:
                    if webrtc is None:
                        webrtc = WebRTCProcessor(agc_level=agc, ns_level=ns)
                    else:
                        webrtc.update_settings(agc, ns)
                    audio_chunk = webrtc.process(audio_chunk)
                    if not audio_chunk:
                        continue

                if state.satellite is None or not hasattr(state.satellite, "_is_streaming_audio"):
                    continue

                # WAKE WORD
                if (not wake_words) or (state.wake_words_changed and state.wake_words):
                    # Update list of wake word models to process
                    state.wake_words_changed = False
                    wake_words = select_active_wake_words(state)

                    # TODO: Load default stop word value from json into state and preferences missing.

                    apply_sensitivities(state, wake_words)
                    has_oww = any(isinstance(word.model, OpenWakeWord) for word in wake_words)

                    # Sync entity states after threshold values were updated
                    if state.satellite is not None:
                        _LOGGER.debug("Updating WebUI entities with new threshold values")

                        # Wake Word 1
                        if state.satellite.state.sensitivity_1_number_entity is not None:
                            _LOGGER.debug("  → Syncing Wake Word 1 entity to value %.3f", state.wake_word_1_threshold)
                            state.satellite.state.sensitivity_1_number_entity.sync_with_state()
                            _LOGGER.debug("  ✅ Wake Word 1 entity now has value %.3f", state.satellite.state.sensitivity_1_number_entity.value)

                        # Wake Word 2
                        if state.satellite.state.sensitivity_2_number_entity is not None:
                            _LOGGER.debug("  → Syncing Wake Word 2 entity to value %.3f", state.wake_word_2_threshold)
                            state.satellite.state.sensitivity_2_number_entity.sync_with_state()
                            _LOGGER.debug("  ✅ Wake Word 2 entity now has value %.3f", state.satellite.state.sensitivity_2_number_entity.value)

                        # Stop Word
                        if state.satellite.state.stop_sensitivity_number_entity is not None:
                            _LOGGER.debug("  → Syncing Stop Word entity to value %.3f", state.stop_word_threshold)
                            state.satellite.state.stop_sensitivity_number_entity.sync_with_state()
                            _LOGGER.debug("  ✅ Stop Word entity now has value %.3f", state.satellite.state.stop_sensitivity_number_entity.value)

                        _LOGGER.debug("All sensitivity entities synced successfully")

                        # Force push new state to connected Home Assistant instance
                        if state.satellite is not None:
                            try:
                                _LOGGER.debug("Pushing updated state values to Home Assistant")
                                for entity in [
                                    state.satellite.state.sensitivity_1_number_entity,
                                    state.satellite.state.sensitivity_2_number_entity,
                                    state.satellite.state.stop_sensitivity_number_entity,
                                ]:
                                    if entity is not None:
                                        state.satellite.send_messages([NumberStateResponse(key=entity.key, state=entity.value)])  # type: ignore[attr-defined]
                                        _LOGGER.debug("  → Pushed value %.3f for entity %d", entity.value, entity.key)
                            except Exception as e:
                                _LOGGER.debug("Could not push state (no client connected yet): %s", e)

                    # TODO: Save settings: At this moment settings are only saved when changed in the UI. Means that the default value can change while updating since its not saved in preferences.

                    if micro_features is None:
                        micro_features = MicroWakeWordFeatures()

                    if has_oww and (oww_features is None):
                        oww_features = OpenWakeWordFeatures.from_builtin()

                try:
                    # data=ch0 (after WebRTC gain/noise suppression), data2=ch0 before any WebRTC processing
                    audio_chunk_2 = audio_chunk_unprocessed
                    state.satellite.handle_audio(audio_chunk, audio_chunk_2)

                    assert micro_features is not None
                    micro_inputs.clear()
                    micro_inputs.extend(micro_features.process_streaming(audio_chunk))

                    wake_word_running = gate_wake_words(wake_words, oww_features, state.satellite.pipeline_active, wake_word_running)
                    if wake_word_running:
                        if has_oww:
                            assert oww_features is not None
                            oww_inputs.clear()
                            oww_inputs.extend(oww_features.process_streaming(audio_chunk))

                        now = time.monotonic()
                        detections, last_active = process_wake_words(state, wake_words, micro_inputs, oww_inputs, last_active, now)
                        for wake_word, detection in detections:
                            state.satellite.wakeup(wake_word, detection)

                    stopped, stop_word_running = process_stop_word(state, micro_inputs, stop_word_running)

                    if stopped and (state.stop_word.id in state.active_wake_words) and not state.muted:
                        _LOGGER.debug("Stop word detected")
                        state.satellite.stop()
                except Exception:  # pylint: disable=broad-except
                    _LOGGER.exception("Unexpected error handling audio")
    except Exception:  # pylint: disable=broad-except
        _LOGGER.exception("Unexpected error processing audio, stopping because no audio can be recorded anymore")
        logging.shutdown()
        os._exit(1)  # pylint: disable=protected-access


# -----------------------------------------------------------------------------


def _float_to_pcm16(samples: np.ndarray, scalar: float) -> bytes:
    """
    Scale float samples in [-1, 1] and convert them to little-endian 16-bit PCM bytes.
    :param samples: Float32 samples of a single channel.
    :param scalar: Gain applied before clipping.
    """
    scaled = samples * scalar
    np.clip(scaled, -1.0, 1.0, out=scaled)
    scaled *= 32767.0
    return scaled.astype("<i2").tobytes()


class _ReconnectingRecorder:
    """Microphone recorder that reopens its stream when the audio server reports a failure."""

    def __init__(self, mic, n_channels: int, block_size: int) -> None:
        """
        :param mic: soundcard microphone to record from.
        :param n_channels: Number of channels to record.
        :param block_size: Frames per recorded block.
        """
        self._mic = mic
        self._n_channels = n_channels
        self._block_size = block_size
        self._recorder: Any = None

    def __enter__(self) -> "_ReconnectingRecorder":
        self._open()
        return self

    def __exit__(self, *exc_info) -> None:
        self._close()

    def record(self, frames: int) -> np.ndarray:
        """
        Record a block, reopening the microphone stream if it failed.
        :param frames: Number of frames to record.
        """
        try:
            return self._recorder.record(frames)  # type: ignore[no-any-return]
        except RuntimeError as err:
            self._recover(err)
            return self._recorder.record(frames)  # type: ignore[no-any-return]

    def _open(self) -> None:
        self._recorder = self._mic.recorder(samplerate=16000, channels=self._n_channels, blocksize=self._block_size)
        self._recorder.__enter__()  # pylint: disable=unnecessary-dunder-call

    def _close(self) -> None:
        recorder, self._recorder = self._recorder, None
        if recorder is not None:
            try:
                recorder.__exit__(None, None, None)
            except Exception:  # pylint: disable=broad-except
                _LOGGER.debug("Ignoring error while closing failed microphone stream", exc_info=True)

    def _recover(self, err: Exception) -> None:
        """
        Reopen the microphone stream immediately and, if that fails, once more after a short delay.
        :param err: Error that interrupted recording.
        """
        name = getattr(self._mic, "name", self._mic)
        _LOGGER.error(
            "Recording from microphone '%s' failed: %s. The device may have been suspended, unplugged or the audio server restarted. Trying to reconnect...",
            name,
            err,
        )
        last_err: Exception = err
        for attempt in (1, 2):
            if attempt == 2:
                time.sleep(_MIC_RETRY_DELAY_SECONDS)
            self._close()
            try:
                self._refresh_mic()
                self._open()
                _LOGGER.info("Microphone '%s' reconnected (attempt %d)", name, attempt)
                return
            except Exception as open_err:  # pylint: disable=broad-except
                last_err = open_err
                _LOGGER.warning("Reconnecting microphone '%s' failed (attempt %d): %s", name, attempt, open_err)

        self._close()
        raise RuntimeError(f"Microphone '{name}' did not recover after 2 reconnect attempts: {last_err}") from last_err

    def _refresh_mic(self) -> None:
        """Look up the microphone again, because the device object can be stale after a suspend or replug."""
        mic_id = getattr(self._mic, "id", None)
        if mic_id is not None:
            self._mic = sc.get_microphone(mic_id)


def run():
    asyncio.run(main())


if __name__ == "__main__":
    run()
