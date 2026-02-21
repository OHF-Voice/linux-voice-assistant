#!/usr/bin/env python3
import argparse
import asyncio
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
from pymicro_wakeword import MicroWakeWord, MicroWakeWordFeatures
from pyopen_wakeword import OpenWakeWord, OpenWakeWordFeatures

from .models import AvailableWakeWord, Preferences, ServerState, WakeWordType
from .mpv_player import MpvMediaPlayer
from .satellite import VoiceSatelliteProtocol
from getmac import get_mac_address
from .zeroconf import HomeAssistantZeroconf
from .util import get_default_interface
from .util import get_default_ipv4
from .util import get_version
from .util import get_esphome_version

_LOGGER = logging.getLogger(__name__)
_MODULE_DIR = Path(__file__).parent
_REPO_DIR = _MODULE_DIR.parent
_WAKEWORDS_DIR = _REPO_DIR / "wakewords"
_SOUNDS_DIR = _REPO_DIR / "sounds"


# -----------------------------------------------------------------------------


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--name"
    )
    parser.add_argument(
        "--audio-input-device",
        help="soundcard name for input device (see --list-input-devices)",
    )
    parser.add_argument(
        "--list-input-devices",
        action="store_true",
        help="List audio input devices and exit",
    )
    parser.add_argument(
        "--audio-input-block-size", 
        type=int, 
        default=1024
    )
    parser.add_argument(
        "--audio-output-device",
        help="mpv name for output device (see --list-output-devices)",
    )
    parser.add_argument(
        "--list-output-devices",
        action="store_true",
        help="List audio output devices and exit",
    )
    parser.add_argument(
        "--wake-word-dir",
        default=[_WAKEWORDS_DIR],
        action="append",
        help="Directory with wake word models (.tflite) and configs (.json)",
    )
    parser.add_argument(
        "--wake-model", 
        default="okay_nabu", 
        help="Id of active wake model"
    )
    parser.add_argument(
        "--stop-model", 
        default="stop", 
        help="Id of stop model"
    )
    parser.add_argument(
        "--download-dir",
        default=_REPO_DIR / "local",
        help="Directory to download custom wake word models, etc.",
    )
    parser.add_argument(
        "--refractory-seconds",
        default=2.0,
        type=float,
        help="Seconds before wake word can be activated again",
    )
    parser.add_argument(
        "--wakeup-sound", 
        default=str(_SOUNDS_DIR / "wake_word_triggered.flac")
    )
    parser.add_argument(
        "--timer-finished-sound", 
        default=str(_SOUNDS_DIR / "timer_finished.flac")
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
    parser.add_argument("--preferences-file", default=_REPO_DIR / "preferences.json")
    parser.add_argument(
        "--host",
        help="IP-Address for ESPHome server", # 0.0.0.0 is IPv4, None is all interfaces
    )
    parser.add_argument(
        "--network-interface",
        help="Network interface to use for ESPHome server (default: will be automatically detected)",
    )
    # Note that default port is also set in docker-entrypoint.sh
    parser.add_argument(
        "--port", type=int, 
        default=6053, 
        help="Port for ESPHome server (default: 6053)"
    )
    parser.add_argument(
        "--enable-thinking-sound",
        action="store_true",
        help="Enable thinking sound on startup",
    )
    parser.add_argument(
        "--debug", action="store_true", help="Print DEBUG messages to console"
    )
    args = parser.parse_args()

    if args.list_input_devices:
        print("Input devices")
        print("=" * 13)
        for idx, mic in enumerate(sc.all_microphones()):
            print(f"[{idx}]", mic.name)
        return

    if args.list_output_devices:
        from mpv import MPV

        player = MPV()
        print("Output devices")
        print("=" * 14)

        for speaker in player.audio_device_list:  # type: ignore
            print(speaker["name"] + ":", speaker["description"])
        return

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    _LOGGER.debug(args)

    # Resolve network interface for mac-adress detection
    if not args.network_interface:
        print("No network interface specified, try to detect default interface")
        network_interface = get_default_interface()
        print(f"Default interface detected:", network_interface)
    else:
        print("Network interface specified")
        network_interface = args.network_interface
        print(f"Using network interface: ", network_interface)

    # Resolve ip_address where the application will be listening
    if not args.host:
        print("No host (ip-address) specified, try to detect IP-Address")
        host_ip_address = get_default_ipv4(network_interface)
        print(f"IP-Address detected: ", host_ip_address)
    else:
        print("Host specified")
        print(f"Using host: ", args.host)
        host_ip_address = args.host

    # Resolve mac
    mac_address = get_mac_address(interface=network_interface)
    mac_address_clean = mac_address.replace(":", "")

    # Resolve name
    if not args.name:
        print("No friendly name specified, try to autogenerate name")
        friendly_name = f"LVA - {mac_address_clean}"
        print(f"Friendly name autogenerated: {friendly_name}")
    else:
        print("Friendly name specified")
        print(f"Using friendly name: {args.name}")
        friendly_name = args.name

    mac_no_colon = mac_address.replace(":", "").lower()
    device_name = f"lva-{mac_no_colon}"
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
    available_wake_words: Dict[str, AvailableWakeWord] = {}

    for wake_word_dir in wake_word_dirs:
        for model_config_path in wake_word_dir.glob("*.json"):
            model_id = model_config_path.stem
            if model_id == args.stop_model:
                # Don't show stop model as an available wake word
                continue

            with open(model_config_path, "r", encoding="utf-8") as model_config_file:
                model_config = json.load(model_config_file)
                model_type = WakeWordType(model_config["type"])
                if model_type == WakeWordType.OPEN_WAKE_WORD:
                    wake_word_path = model_config_path.parent / model_config["model"]
                else:
                    wake_word_path = model_config_path

                available_wake_words[model_id] = AvailableWakeWord(
                    id=model_id,
                    type=WakeWordType(model_type),
                    wake_word=model_config["wake_word"],
                    trained_languages=model_config.get("trained_languages", []),
                    wake_word_path=wake_word_path,
                )

    _LOGGER.debug("Available wake words: %s", list(sorted(available_wake_words.keys())))

    # Load preferences
    preferences_path = Path(args.preferences_file)
    if preferences_path.exists():
        _LOGGER.debug("Loading preferences: %s", preferences_path)
        with open(preferences_path, "r", encoding="utf-8") as preferences_file:
            preferences_dict = json.load(preferences_file)
            preferences = Preferences(**preferences_dict)
    else:
        preferences = Preferences()

    if args.enable_thinking_sound:
        preferences.thinking_sound = 1

    # Load wake/stop models
    active_wake_words: Set[str] = set()
    wake_models: Dict[str, Union[MicroWakeWord, OpenWakeWord]] = {}
    if preferences.active_wake_words:
        # Load preferred models
        for wake_word_id in preferences.active_wake_words:
            wake_word = available_wake_words.get(wake_word_id)
            if wake_word is None:
                _LOGGER.warning("Unrecognized wake word id: %s", wake_word_id)
                continue

            _LOGGER.debug("Loading wake model: %s", wake_word_id)
            wake_models[wake_word_id] = wake_word.load()
            active_wake_words.add(wake_word_id)

    if not wake_models:
        # Load default model
        wake_word_id = args.wake_model
        wake_word = available_wake_words[wake_word_id]

        _LOGGER.debug("Loading wake model: %s", wake_word_id)
        wake_models[wake_word_id] = wake_word.load()
        active_wake_words.add(wake_word_id)

    # TODO: allow openWakeWord for "stop"
    stop_model: Optional[MicroWakeWord] = None
    for wake_word_dir in wake_word_dirs:
        stop_config_path = wake_word_dir / f"{args.stop_model}.json"
        if not stop_config_path.exists():
            continue

        _LOGGER.debug("Loading stop model: %s", stop_config_path)
        stop_model = MicroWakeWord.from_config(stop_config_path)
        break

    assert stop_model is not None

    state = ServerState(
        name=device_name,
        friendly_name=friendly_name,
        network_interface=network_interface,
        mac_address=get_mac_address(interface=network_interface),
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
        download_dir=args.download_dir,
    )

    if args.enable_thinking_sound:
        state.save_preferences()

    process_audio_thread = threading.Thread(
        target=process_audio,
        args=(state, mic, args.audio_input_block_size),
        daemon=True,
    )
    process_audio_thread.start()

    loop = asyncio.get_running_loop()
    server = await loop.create_server(
        lambda: VoiceSatelliteProtocol(state), host=host_ip_address, port=args.port
    )

    # Auto discovery (zeroconf, mDNS)
    discovery = HomeAssistantZeroconf(port=args.port, name=state.name, mac_address=state.mac_address, host_ip_address=host_ip_address)
    await discovery.register_server()

    try:
        async with server:
            _LOGGER.info("Server started (host=%s, port=%s)", host_ip_address, args.port)
            await server.serve_forever()
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

    try:
        _LOGGER.debug("Opening audio input device: %s", mic.name)
        with mic.recorder(samplerate=16000, channels=1, blocksize=block_size) as mic_in:
            while True:
                audio_chunk_array = mic_in.record(block_size).reshape(-1)
                audio_chunk = (
                    (np.clip(audio_chunk_array, -1.0, 1.0) * 32767.0)
                    .astype("<i2")  # little-endian 16-bit signed
                    .tobytes()
                )

                if state.satellite is None:
                    continue

                if (not wake_words) or (state.wake_words_changed and state.wake_words):
                    # Update list of wake word models to process
                    state.wake_words_changed = False
                    wake_words = [
                        ww
                        for ww in state.wake_words.values()
                        if ww.id in state.active_wake_words
                    ]

                    has_oww = False
                    for wake_word in wake_words:
                        if isinstance(wake_word, OpenWakeWord):
                            has_oww = True

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

                    for wake_word in wake_words:
                        activated = False
                        if isinstance(wake_word, MicroWakeWord):
                            for micro_input in micro_inputs:
                                if wake_word.process_streaming(micro_input):
                                    activated = True
                        elif isinstance(wake_word, OpenWakeWord):
                            for oww_input in oww_inputs:
                                for prob in wake_word.process_streaming(oww_input):
                                    if prob > 0.5:
                                        activated = True

                        if activated and not state.muted:
                            # Check refractory
                            now = time.monotonic()
                            if (last_active is None) or (
                                (now - last_active) > state.refractory_seconds
                            ):
                                state.satellite.wakeup(wake_word)
                                last_active = now

                    # Always process to keep state correct
                    stopped = False
                    for micro_input in micro_inputs:
                        if state.stop_word.process_streaming(micro_input):
                            stopped = True

                    if (
                        stopped
                        and (state.stop_word.id in state.active_wake_words)
                        and not state.muted
                    ):
                        state.satellite.stop()
                except Exception:
                    _LOGGER.exception("Unexpected error handling audio")
    except Exception:
        _LOGGER.exception("Unexpected error processing audio")
        sys.exit(1)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(main())
