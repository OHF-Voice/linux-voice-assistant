#!/usr/bin/env python3
import argparse
import asyncio
import errno
import json
import logging
import sys
import threading
import time
from pathlib import Path
from queue import Queue
from typing import Dict, List, Optional, Set, Union

import numpy as np
import soundcard as sc
from getmac import get_mac_address  # type: ignore
from pymicro_wakeword import MicroWakeWord, MicroWakeWordFeatures
from pyopen_wakeword import OpenWakeWord, OpenWakeWordFeatures

from .models import Preferences, ServerState
from .wake_word import find_available_wake_words, load_wake_models, load_stop_model

from .mpv_player import MpvMediaPlayer
from .satellite import VoiceSatelliteProtocol
from .util import (
    get_default_interface,
    get_default_ipv4,
    get_esphome_version,
    get_version,
)
from .webrtc import WebRTCProcessor
from .zeroconf import HomeAssistantZeroconf

_LOGGER = logging.getLogger(__name__)
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
        # todo
    )
    parser.add_argument(
        "--audio-output-device",
        help="Name for the audio output device (see --list-output-devices)",
    )
    parser.add_argument(
        "--list-output-devices",
        action="store_true",
        help="List audio output devices and exit",
    )
    parser.add_argument("--mic-auto-gain", type=int, default=0, choices=list(range(32)))
    parser.add_argument("--mic-noise-suppression", type=int, default=0, choices=(0, 1, 2, 3, 4))
    parser.add_argument(
        "--wake-word-dir",
        default=[_WAKEWORDS_DIR],
        action="append",
        help="Directory with wake word models (.tflite) and configuration (.json)",
    )
    parser.add_argument(
        "--wake-model",
        default="okay_nabu",
        help="File name of the first active wake model",
    )
    parser.add_argument(
        "--stop-model",
        default="stop",
        help="File name of the stop model",
    )
    parser.add_argument(
        "--download-dir",
        default=_REPO_DIR / "local",
        help="Directory to download custom wake word models to",
    )
    parser.add_argument(
        "--refractory-seconds",
        default=2.0,
        type=float,
        help="Seconds before wake word can be activated again",
    )
    parser.add_argument(
        "--wakeup-sound",
        default=str(_SOUNDS_DIR / "wake_word_triggered.flac"),
        help="Directory and file name for wake sound (when you say the wake word)",
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
        "--preferences-file",
        default=_REPO_DIR / "preferences.json",
        help="Directory and file name for the file where the preferences are stored in JSON format",
    )
    parser.add_argument(
        "--host",
        help="Optional host IP address to bind to (default: Autodetected by network interface)",  # 0.0.0.0 is IPv4, None is all interfaces
    )
    parser.add_argument(
        "--network-interface",
        help="Network interface the application will be listening on (default: will be automatically detected by gateway)",
    )
    # Note that default port is also set in docker-entrypoint.sh
    parser.add_argument(
        "--port",
        type=int,
        default=6053,
        help="Port the application is listenening on (default: 6053)",
    )
    parser.add_argument(
        "--enable-thinking-sound",
        action="store_true",
        help="Enable thinking finish sound, when the assistant is done thinking and needed more time to process",
    )
    parser.add_argument(
        "--timer-max-ring-seconds",
        type=float,
        default=900.0,  # 15 minutes
        help="Seconds before a ringing timer auto-stops (default: 900)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Add this to enable debug logging",
    )
    parser.add_argument(
        "--output-only",
        action="store_true",
        help="Enable output only mode",
    )
    args = parser.parse_args()

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

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    _LOGGER.debug(args)

    # Resolve network interface for mac-address detection
    if not args.network_interface:
        print("No network interface specified, try to detect default interface")
        network_interface = get_default_interface()
        print(f"Default interface detected: {network_interface}")
    else:
        print("Network interface specified")
        network_interface = args.network_interface
        print(f"Using network interface: {network_interface}")

    # Resolve ip_address where the application will be listening
    if not args.host:
        print("No host (ip-address) specified, try to detect IP-Address")
        host_ip_address = get_default_ipv4(network_interface)
        print(f"IP-Address detected: {host_ip_address}")
    else:
        print("Host specified")
        print(f"Using host: {args.host}")
        host_ip_address = args.host

    # Resolve mac
    if not (mac_address := get_mac_address(interface=network_interface)):
        print("No Mac address was found, app stopped.")
        sys.exit(1)
    mac_address_clean = mac_address.replace(":", "").lower()

    # Resolve name
    if not args.name:
        print("No friendly name specified, try to autogenerate name")
        friendly_name = f"LVA - {mac_address_clean}"
        print(f"Friendly name autogenerated: {friendly_name}")
    else:
        print("Friendly name specified")
        print(f"Using friendly name: {args.name}")
        friendly_name = args.name

    device_name = f"lva-{mac_address_clean}"

    print(f"Device name: {device_name}")

    # Resolve version
    version = get_version()
    print(f"Version: {version}")

    # Resolve esphome version
    esphome_version = get_esphome_version()
    print(f"ESPHome api version: {esphome_version}")

    # Resolve download dir
    args.download_dir = Path(args.download_dir)
    args.download_dir.mkdir(parents=True, exist_ok=True)

    # Resolve microphone
    if args.audio_input_device is not None:
        try:
            args.audio_input_device = int(args.audio_input_device)
        except ValueError:
            pass

        mic = sc.get_microphone(args.audio_input_device)
    else:
        mic = sc.default_microphone()

    # Load available wake words
    wake_word_dirs = [Path(ww_dir) for ww_dir in args.wake_word_dir]
    wake_word_dirs.append(args.download_dir / "external_wake_words")
    available_wake_words = find_available_wake_words(wake_word_dirs, args.stop_model)

    # Load preferences
    preferences_path = Path(args.preferences_file)
    if preferences_path.exists():
        _LOGGER.debug("Loading preferences: %s", preferences_path)
        with open(preferences_path, "r", encoding="utf-8") as preferences_file:
            preferences_dict = json.load(preferences_file)
            preferences = Preferences(**preferences_dict)
    else:
        preferences = Preferences()

    # Load volume from preferences on startup, and ensure it's between 0.0 and 1.0
    initial_volume = preferences.volume if preferences.volume is not None else 1.0
    initial_volume = max(0.0, min(1.0, float(initial_volume)))
    preferences.volume = initial_volume

    if args.enable_thinking_sound:
        preferences.thinking_sound = 1

    if args.mic_auto_gain or args.mic_noise_suppression:
        try:
            import webrtc_noise_gain  # type: ignore[import-untyped] # noqa: F401
        except ImportError:
            _LOGGER.exception("Extras for webrtc are not installed")
            sys.exit(1)

    if args.mic_auto_gain > 0:
        preferences.mic_auto_gain = args.mic_auto_gain

    if args.mic_noise_suppression > 0:
        preferences.mic_noise_suppression = args.mic_noise_suppression

    # Load wake/stop models
    wake_models, active_wake_words, fallback_used = load_wake_models(available_wake_words, preferences.active_wake_words, args.wake_model)

    # TODO: allow openWakeWord for "stop"
    stop_model = load_stop_model(wake_word_dirs, args.stop_model)
    assert stop_model is not None

    state = ServerState(
        name=device_name,
        friendly_name=friendly_name,
        network_interface=network_interface,
        mac_address=mac_address,
        ip_address=host_ip_address,
        version=version,
        esphome_version=esphome_version,
        audio_queue=Queue(),
        entities=[],
        available_wake_words=available_wake_words,
        wake_words=wake_models,
        active_wake_words=active_wake_words,
        stop_word=stop_model,
        music_player=MpvMediaPlayer(device=args.audio_output_device),
        tts_player=MpvMediaPlayer(device=args.audio_output_device),
        wakeup_sound=args.wakeup_sound,
        timer_finished_sound=args.timer_finished_sound,
        processing_sound=args.processing_sound,
        mute_sound=args.mute_sound,
        unmute_sound=args.unmute_sound,
        preferences=preferences,
        preferences_path=preferences_path,
        refractory_seconds=args.refractory_seconds,
        output_only=args.output_only,
        download_dir=args.download_dir,
        volume=initial_volume,
        mic_volume=preferences.mic_volume,
        mic_auto_gain=preferences.mic_auto_gain,
        mic_noise_suppression=preferences.mic_noise_suppression,
        timer_max_ring_seconds=args.timer_max_ring_seconds,
    )

    if fallback_used:
        # Fallback auf Default Model wurde verwendet, speichere als aktive Wake Words
        _LOGGER.debug("Fallback wurde verwendet, speichere Standard Wake Words in Preferences")
        state.preferences.active_wake_words = list(active_wake_words)
        state.active_wake_words = active_wake_words
        state.wake_words = wake_models
        state.save_preferences()
        state.wake_words_changed = True

    if args.enable_thinking_sound or args.mic_auto_gain or args.mic_noise_suppression:
        state.save_preferences()

    initial_volume_percent = int(round(initial_volume * 100))
    state.music_player.set_volume(initial_volume_percent)
    state.tts_player.set_volume(initial_volume_percent)

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
    except Exception as e:
        _LOGGER.critical("❌ FATAL ERROR in VoiceSatelliteProtocol initialization!", exc_info=True)
        _LOGGER.critical("Program will exit immediately - fix the error above first!")
        sys.exit(1)

    while attempt <= max_attempts:
        try:
            server = await loop.create_server(lambda: VoiceSatelliteProtocol(state), host=host_ip_address, port=args.port)
            break  # connect successful, exit the loop
        except OSError as err:
            message = err.strerror or str(err)
            if err.errno == errno.EADDRINUSE:
                message = "address already in use"
            if attempt < max_attempts:
                _LOGGER.warning("Attempt %d/%d failed to bind on address (%s, %s): %s. Retrying in 1 second...", attempt, max_attempts, host_ip_address, args.port, message)
                await asyncio.sleep(1)
                attempt += 1
            else:
                _LOGGER.exception("All %d attempts failed to bind on address (%s, %s): %s", max_attempts, host_ip_address, args.port, message)
                sys.exit(1)

    process_audio_thread = threading.Thread(
        target=process_audio,
        args=(state, mic, args.audio_input_block_size),
        daemon=True,
    )
    process_audio_thread.start()

    # Auto discovery (zeroconf, mDNS)
    discovery = HomeAssistantZeroconf(port=args.port, name=state.name, mac_address=state.mac_address, host_ip_address=host_ip_address)
    await discovery.register_server()

    try:
        async with server:  # type: ignore
            _LOGGER.info("Server started (host=%s, port=%s)", host_ip_address, args.port)
            await server.serve_forever()  # type: ignore
    except KeyboardInterrupt:
        pass
    finally:
        state.audio_queue.put_nowait(None)
        process_audio_thread.join()

    _LOGGER.debug("Server stopped")


# -----------------------------------------------------------------------------


def process_audio(state: ServerState, mic, block_size: int):
    """Process audio chunks from the microphone."""

    wake_words: List[Union[MicroWakeWord, OpenWakeWord]] = []
    micro_features: Optional[MicroWakeWordFeatures] = None
    micro_inputs: List[np.ndarray] = []

    oww_features: Optional[OpenWakeWordFeatures] = None
    oww_inputs: List[np.ndarray] = []
    has_oww = False

    last_active: Optional[float] = None
    webrtc: Optional[WebRTCProcessor] = None

    try:
        _LOGGER.debug("Opening audio input device: %s", mic.name)
        with mic.recorder(samplerate=16000, channels=1, blocksize=block_size) as mic_in:
            while True:
                audio_chunk_array = mic_in.record(block_size).reshape(-1)
                # little-endian 16-bit signed
                mic_vol_scalar = max(0.1, min(1.0, state.mic_volume / 100.0))
                audio_chunk = (np.clip(audio_chunk_array * mic_vol_scalar, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
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
                    wake_words = [ww for ww in state.wake_words.values() if ww.id in state.active_wake_words]

                    # TODO: Load default stop word value from json into state and preferences missing.

                    has_oww = False
                    for idx, wake_word in enumerate(wake_words):

                        # Load default threshold from model json
                        wake_word_id = wake_word.id if hasattr(wake_word, "id") else next(iter(state.wake_words.keys()))
                        available_word = state.available_wake_words.get(wake_word_id)
                        # _LOGGER.debug("word= %s", state.available_wake_words.get(wake_word_id))
                        default_threshold = available_word.probability_cutoff if available_word else 0.7
                        _LOGGER.debug("Using default threshold %.3f for wake word '%s' from model config", default_threshold, wake_word_id)
                        # Check preferences override
                        if idx == 0:
                            old_val = state.wake_word_1_threshold
                            if state.preferences.wake_word_1_sensitivity is not None:
                                state.wake_word_1_threshold = state.preferences.wake_word_1_sensitivity
                            else:
                                state.wake_word_1_threshold = default_threshold
                            _LOGGER.debug("Wake Word 1 threshold set to %.3f (was %.3f, preferences: %s)", state.wake_word_1_threshold, old_val, state.preferences.wake_word_1_sensitivity)
                        elif idx == 1:
                            old_val = state.wake_word_2_threshold
                            if state.preferences.wake_word_2_sensitivity is not None:
                                state.wake_word_2_threshold = state.preferences.wake_word_2_sensitivity
                            else:
                                state.wake_word_2_threshold = default_threshold
                            _LOGGER.debug("Wake Word 2 threshold set to %.3f (was %.3f, preferences: %s)", state.wake_word_2_threshold, old_val, state.preferences.wake_word_2_sensitivity)

                        if isinstance(wake_word, OpenWakeWord):
                            has_oww = True

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
                                        from aioesphomeapi.api_pb2 import NumberStateResponse

                                        state.satellite.send_messages([NumberStateResponse(key=entity.key, state=entity.value)])
                                        _LOGGER.debug("  → Pushed value %.3f for entity %d", entity.value, entity.key)
                            except Exception as e:
                                _LOGGER.debug("Could not push state (no client connected yet): %s", e)

                    # TODO: Save settings: At this moment settings are only saved when changed in the UI. Means that the default value can change while updating since its not saved in preferences.

                    if micro_features is None:
                        micro_features = MicroWakeWordFeatures()

                    if has_oww and (oww_features is None):
                        oww_features = OpenWakeWordFeatures.from_builtin()

                try:
                    state.satellite.handle_audio(audio_chunk)

                    assert micro_features is not None
                    micro_inputs.clear()
                    micro_inputs.extend(micro_features.process_streaming(audio_chunk))

                    if has_oww:
                        assert oww_features is not None
                        oww_inputs.clear()
                        oww_inputs.extend(oww_features.process_streaming(audio_chunk))

                    for wake_word_index, wake_word in enumerate(wake_words):
                        activated = False

                        # Set dynamic threshold depending on wake word index
                        if wake_word_index == 0:
                            threshold = state.wake_word_1_threshold
                            # _LOGGER.debug("Set wake word %d probability cutoff to %.3f", wake_word_index+1, state.wake_word_1_threshold)
                        elif wake_word_index == 1:
                            threshold = state.wake_word_2_threshold
                            # _LOGGER.debug("Set wake word %d probability cutoff to %.3f", wake_word_index+1, state.wake_word_2_threshold)
                        else:
                            threshold = 0.7
                            # _LOGGER.debug("Set wake word %d probability cutoff to fallback value 0.7", wake_word_index+1)

                        if isinstance(wake_word, MicroWakeWord):
                            # No debugging when no detection
                            wake_word.debug_probabilities = False

                            # set microWakeWord cutoff
                            wake_word.probability_cutoff = threshold

                            for micro_input in micro_inputs:
                                if wake_word.process_streaming(micro_input):
                                    wake_word.debug_probabilities = True
                                    activated = True
                        elif isinstance(wake_word, OpenWakeWord):
                            for oww_input in oww_inputs:
                                for prob in wake_word.process_streaming(oww_input):
                                    if prob > threshold:
                                        _LOGGER.debug("Wake word '%s' activated (probability %.3f exceeded threshold %.3f)", wake_word.wake_word, prob, threshold)
                                        activated = True

                        if activated and not state.muted:
                            # Check refractory
                            now = time.monotonic()
                            if (last_active is None) or ((now - last_active) > state.refractory_seconds):
                                state.satellite.wakeup(wake_word)
                                last_active = now

                    # Always process to keep state correct
                    stopped = False

                    # No debugging when no detection
                    state.stop_word.debug_probabilities = False

                    # Apply stop word sensitivity threshold
                    state.stop_word.probability_cutoff = state.stop_word_threshold
                    # _LOGGER.debug("Set stop word probability cutoff to %.3f", state.stop_word_threshold)
                    for micro_input in micro_inputs:
                        if state.stop_word.process_streaming(micro_input):
                            state.stop_word.debug_probabilities = True
                            stopped = True

                    if stopped and (state.stop_word.id in state.active_wake_words) and not state.muted:
                        _LOGGER.debug("Stop word detected")
                        state.satellite.stop()
                except Exception:
                    _LOGGER.exception("Unexpected error handling audio")
    except Exception:
        _LOGGER.exception("Unexpected error processing audio")
        sys.exit(1)


# -----------------------------------------------------------------------------


def run():
    asyncio.run(main())


if __name__ == "__main__":
    run()
