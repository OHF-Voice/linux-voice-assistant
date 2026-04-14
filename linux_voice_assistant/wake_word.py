#!/usr/bin/env python3
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set, Union

from pymicro_wakeword import MicroWakeWord
from pyopen_wakeword import OpenWakeWord

from .models import AvailableWakeWord, WakeWordType

_LOGGER = logging.getLogger(__name__)


def find_available_wake_words(wake_word_dirs: List[Path], stop_model_id: str) -> Dict[str, AvailableWakeWord]:
    """
    Searches all available wake words in the specified directories.
    Loads configurations and creates AvailableWakeWord objects.

    Args:
        wake_word_dirs: List of directories to search for wake words
        stop_model_id: ID of the stop model which will not be listed as available wake word

    Returns:
        Dictionary with wake word ID as key and AvailableWakeWord object as value
    """
    available_wake_words: Dict[str, AvailableWakeWord] = {}

    _LOGGER.debug("Searching for wake words in directories: %s", [str(d) for d in wake_word_dirs])

    for wake_word_dir in wake_word_dirs:
        _LOGGER.debug("Checking directory: %s (exists: %s)", wake_word_dir, wake_word_dir.exists())

        config_files = list(wake_word_dir.glob("*.json"))
        _LOGGER.debug("Found %d JSON configuration files in %s", len(config_files), wake_word_dir)

        for model_config_path in config_files:
            _LOGGER.debug("Processing configuration file: %s", model_config_path)

            model_id = model_config_path.stem
            if model_id == stop_model_id:
                # Skip stop model, do not show as available wake word
                _LOGGER.debug("Skipping stop model: %s", model_id)
                continue

            with open(model_config_path, "r", encoding="utf-8") as model_config_file:
                model_config = json.load(model_config_file)
                model_type = WakeWordType(model_config["type"])

                _LOGGER.debug("Model %s is of type: %s", model_id, model_type)

                if model_type == WakeWordType.OPEN_WAKE_WORD:
                    wake_word_path = model_config_path.parent / model_config["model"]
                else:
                    wake_word_path = model_config_path

                _LOGGER.debug("Model path resolved to: %s (exists: %s)", wake_word_path, wake_word_path.exists())

                # Get type specific configuration
                type_config = model_config.get(model_type.value, {})

                _LOGGER.debug("Type specific config for %s (%s): %s", model_id, model_type.value, type_config)

                available_wake_words[model_id] = AvailableWakeWord(
                    id=model_id,
                    type=WakeWordType(model_type),
                    wake_word=model_config["wake_word"],
                    trained_languages=model_config.get("trained_languages", []),
                    wake_word_path=wake_word_path,
                    probability_cutoff=type_config.get("probability_cutoff", 0.7),
                )
                _LOGGER.debug("Successfully registered wake word: %s", model_id)

    _LOGGER.debug("Total available wake words found: %d", len(available_wake_words))
    _LOGGER.debug("Available wake words: %s", list(sorted(available_wake_words.keys())))
    return available_wake_words


def load_wake_models(
    available_wake_words: Dict[str, AvailableWakeWord], active_wake_word_ids: Optional[List[str]], default_wake_word_id: str
) -> tuple[Dict[str, Union[MicroWakeWord, OpenWakeWord]], Set[str], bool]:
    """
    Loads the specified wake word models.

    If no active wake words are provided, the default model will be loaded.

    Args:
        available_wake_words: Dictionary with all available wake words
        active_wake_word_ids: List of IDs of wake words to load (may be None)
        default_wake_word_id: ID of the default model which is loaded if no others are specified

    Returns:
        Tuple with (Dictionary of loaded models, Set of active wake word IDs)
    """
    active_wake_words: Set[str] = set()
    wake_models: Dict[str, Union[MicroWakeWord, OpenWakeWord]] = {}

    _LOGGER.debug("Requested active wake word ids: %s", active_wake_word_ids)
    _LOGGER.debug("Default wake word id: %s", default_wake_word_id)

    if active_wake_word_ids:
        # Load preferred models
        _LOGGER.debug("Loading requested wake word models, count: %d", len(active_wake_word_ids))
        for index, wake_word_id in enumerate(active_wake_word_ids):
            _LOGGER.debug("Processing wake word %d/%d: %s", index + 1, len(active_wake_word_ids), wake_word_id)
            wake_word = available_wake_words.get(wake_word_id)
            if wake_word is None:
                _LOGGER.warning("Unknown wake word ID: %s - skipping", wake_word_id)
                continue

            _LOGGER.debug("Loading wake model: %s (%s)", wake_word_id, wake_word.wake_word)
            try:
                wake_models[wake_word_id] = wake_word.load()
                active_wake_words.add(wake_word_id)
                _LOGGER.debug("✅ Successfully loaded wake model: %s", wake_word_id)
            except Exception as ex:
                _LOGGER.error("❌ Failed to load wake model %s: %s", wake_word_id, ex, exc_info=True)

    if not wake_models:
        # No models loaded, fall back to default model
        _LOGGER.debug("No wake models loaded, falling back to default model")
        wake_word_id = default_wake_word_id
        #wake_word_id2 = "hey_home_assistant"
        wake_word = available_wake_words[wake_word_id]
        #wake_word2 = available_wake_words[wake_word_id2]

        _LOGGER.debug("Loading default wake model 1: %s", wake_word_id)
        #_LOGGER.debug("Loading default wake model 2: %s", wake_word_id2)
        wake_models[wake_word_id] = wake_word.load()
        #wake_models[wake_word_id2] = wake_word2.load()
        active_wake_words.add(wake_word_id)
        #active_wake_words.add(wake_word_id2)
        _LOGGER.debug("Successfully loaded default wake model 1: %s", wake_word_id)
        #_LOGGER.debug("Successfully loaded default wake model 2: %s", wake_word_id2)

    _LOGGER.debug("Loaded %d wake models successfully", len(wake_models))
    _LOGGER.debug("Active wake words: %s", sorted(active_wake_words))

    fallback_used = not active_wake_word_ids or not wake_models

    return wake_models, active_wake_words, fallback_used


def load_stop_model(wake_word_dirs: List[Path], stop_model_id: str) -> Optional[MicroWakeWord]:
    """
    Loads the stop word model.

    Args:
        wake_word_dirs: List of directories to search for the stop model
        stop_model_id: ID of the stop model

    Returns:
        Loaded MicroWakeWord object or None if not found
    """
    _LOGGER.debug("Searching for stop model '%s' in directories: %s", stop_model_id, [str(d) for d in wake_word_dirs])

    for wake_word_dir in wake_word_dirs:
        stop_config_path = wake_word_dir / f"{stop_model_id}.json"
        _LOGGER.debug("Checking stop model path: %s (exists: %s)", stop_config_path, stop_config_path.exists())

        if not stop_config_path.exists():
            continue

        _LOGGER.debug("Found stop model configuration at: %s", stop_config_path)
        _LOGGER.debug("Loading stop model: %s", stop_config_path)

        try:
            model = MicroWakeWord.from_config(stop_config_path)
            _LOGGER.debug("Successfully loaded stop model")
            return model
        except Exception as ex:
            _LOGGER.error("Failed to load stop model from %s: %s", stop_config_path, ex, exc_info=True)

    _LOGGER.warning("Stop model '%s' could not be found in any search directory", stop_model_id)
    return None
