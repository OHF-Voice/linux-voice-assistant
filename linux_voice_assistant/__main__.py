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

from .models import (
    AvailableWakeWord,
    GlobalPreferences,
    Preferences,
    ServerState,
    WakeWordType,
)
from .mpv_player import MpvMediaPlayer
from .satellite import VoiceSatelliteProtocol
from .util import get_mac
from .zeroconf import HomeAssistantZeroconf

_LOGGER = logging.getLogger(__name__)
_MODULE_DIR = Path(__file__).parent
_REPO_DIR = _MODULE_DIR.parent
_WAKEWORDS_DIR = _REPO_DIR / "wakewords"
_SOUNDS_DIR = _REPO_DIR / "sounds"


# -----------------------------------------------------------------------------


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument(
        "--audio-input-device",
        help="soundcard name for input device (see --list-input-devices)",
    )
    parser.add_argument(
        "--list-input-devices",
        action="store_true",
        help="List audio input devices and exit",
    )
    parser.add_argument("--audio-input-block-size", type=int, default=1024)
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
        "--wake-model", default="okay_nabu", help="Id of active wake model"
    )
    parser.add_argument("--stop-model", default="stop", help="Id of stop model")
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
    #
    parser.add_argument(
        "--wakeup-sound", default=str(_SOUNDS_DIR / "wake_word_triggered.flac")
    )
    parser.add_argument(
        "--timer-finished-sound", default=str(_SOUNDS_DIR / "timer_finished.flac")
    )
    #
    parser.add_argument("--preferences-file", default=_REPO_DIR / "preferences.json")
    #
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Address for ESPHome server (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port", type=int, default=6052, help="Port for ESPHome server (default: 6052)"
    )
    parser.add_argument(
        "--mac",
        help="Spoof MAC address exposed to Home Assistant (format: aa:bb:cc:dd:ee:ff or aabbccddeeff)",
    )
    parser.add_argument(
        "--debug", action="store_true", help="Print DEBUG messages to console"
    )
    
    # Parse only --name first to load CLI config defaults
    args, remaining = parser.parse_known_args()
    
    # Load CLI config from last run if it exists (to use as defaults)
    if args.name:
        user_prefs_dir = _REPO_DIR / "preferences" / "user"
        cli_config_path = user_prefs_dir / f"{args.name}_cli.json"
        if cli_config_path.exists():
            try:
                with open(cli_config_path, "r", encoding="utf-8") as f:
                    cli_config = json.load(f)
                    # Filter out non-argument keys
                    cli_only_keys = {"autostart", "__system_info__"}
                    defaults = {k: v for k, v in cli_config.items() if not k.startswith("_") and k not in cli_only_keys}
                    # Convert underscores to hyphens in keys for argparse
                    defaults = {k.replace("_", "-"): v for k, v in defaults.items()}
                    parser.set_defaults(**defaults)
            except Exception:
                pass
    
    # Re-parse with defaults loaded
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

    # Load per-instance preferences and shared global settings
    preferences_path = Path(args.preferences_file)
    global_preferences_path = preferences_path.parent / "ha_settings.json"

    # First load per-instance prefs (active wake words)
    preferences = Preferences()
    migration_globals: Dict[str, object] = {}
    if preferences_path.exists():
        _LOGGER.debug("Loading preferences: %s", preferences_path)
        with open(preferences_path, "r", encoding="utf-8") as preferences_file:
            preferences_dict = json.load(preferences_file)
            # preferences file may contain extra metadata (e.g. CLI args) under
            # other keys. Only extract known fields for the Preferences dataclass.
            if isinstance(preferences_dict, dict):
                active = preferences_dict.get("active_wake_words") or []
                preferences = Preferences(active_wake_words=active)

                # Capture legacy global fields for migration if needed
                for key in ("wake_word_friendly_names", "ha_base_url", "ha_token", "ha_history_entity"):
                    if key in preferences_dict:
                        migration_globals[key] = preferences_dict.get(key)

    # Load global/shared preferences (base URL, token, friendly names)
    global_preferences = GlobalPreferences()
    if global_preferences_path.exists():
        _LOGGER.debug("Loading global preferences: %s", global_preferences_path)
        with open(global_preferences_path, "r", encoding="utf-8") as global_file:
            global_dict = json.load(global_file)
            if isinstance(global_dict, dict):
                global_preferences = GlobalPreferences(
                    wake_word_friendly_names=global_dict.get("wake_word_friendly_names", {}),
                    ha_base_url=global_dict.get("ha_base_url"),
                    ha_token=global_dict.get("ha_token"),
                    ha_history_entity=global_dict.get("ha_history_entity"),
                )

    # Migrate legacy global fields embedded in per-instance file
    if migration_globals and not global_preferences_path.exists():
        _LOGGER.info("Migrating shared HA/friendly-name settings to %s", global_preferences_path)
        global_preferences = GlobalPreferences(
            wake_word_friendly_names=migration_globals.get("wake_word_friendly_names", {}),
            ha_base_url=migration_globals.get("ha_base_url"),
            ha_token=migration_globals.get("ha_token"),
            ha_history_entity=migration_globals.get("ha_history_entity"),
        )
        global_preferences_path.parent.mkdir(parents=True, exist_ok=True)
        with open(global_preferences_path, "w", encoding="utf-8") as global_file:
            json.dump(
                {
                    "wake_word_friendly_names": global_preferences.wake_word_friendly_names,
                    "ha_base_url": global_preferences.ha_base_url,
                    "ha_token": global_preferences.ha_token,
                    "ha_history_entity": global_preferences.ha_history_entity,
                },
                global_file,
                ensure_ascii=False,
                indent=4,
            )

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

    # Normalize MAC if provided so both DeviceInfo and zeroconf use consistent values
    def _normalize_mac_colon(mac: str) -> str:
        m = mac.replace(":", "").replace("-", "").lower()
        if len(m) != 12:
            return mac
        return ":".join(m[i : i + 2] for i in range(0, 12, 2))

    def _normalize_mac_nocolon(mac: str) -> str:
        return _normalize_mac_colon(mac).replace(":", "")

    mac_address_value = get_mac()
    zeroconf_mac_value = None
    if args.mac:
        mac_address_value = _normalize_mac_colon(args.mac)
        zeroconf_mac_value = _normalize_mac_nocolon(args.mac)

    state = ServerState(
        name=args.name,
        mac_address=mac_address_value,
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
        preferences=preferences,
        global_preferences=global_preferences,
        preferences_path=preferences_path,
        global_preferences_path=global_preferences_path,
        refractory_seconds=args.refractory_seconds,
        download_dir=args.download_dir,
    )

    process_audio_thread = threading.Thread(
        target=process_audio,
        args=(state, mic, args.audio_input_block_size),
        daemon=True,
    )
    process_audio_thread.start()

    loop = asyncio.get_running_loop()
    server = await loop.create_server(
        lambda: VoiceSatelliteProtocol(state), host=args.host, port=args.port
    )

    # Auto discovery (zeroconf, mDNS)
    discovery = HomeAssistantZeroconf(port=args.port, name=args.name)
    discovery = HomeAssistantZeroconf(port=args.port, name=args.name, mac=zeroconf_mac_value)
    await discovery.register_server()

    try:
        async with server:
            _LOGGER.info("Server started (host=%s, port=%s)", args.host, args.port)
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

                        if activated:
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

                    if stopped and (state.stop_word.id in state.active_wake_words):
                        state.satellite.stop()
                except Exception:
                    _LOGGER.exception("Unexpected error handling audio")
    except Exception:
        _LOGGER.exception("Unexpected error processing audio")
        sys.exit(1)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(main())
