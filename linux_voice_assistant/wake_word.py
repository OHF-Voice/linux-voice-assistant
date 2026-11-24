#!/usr/bin/env python3
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from pymicro_wakeword import MicroWakeWord
from pyopen_wakeword import OpenWakeWord

from .models import AvailableWakeWord, WakeWordSelection, WakeWordType

_LOGGER = logging.getLogger(__name__)

_ORIGIN_LABELS = {"internal": "Internal", "downloaded": "Download", "custom": "Custom"}


def wake_word_display_name(origin: str, wake_word_type: WakeWordType, wake_word: str) -> str:
    """
    Builds the name of a wake word, for example "Download - OWW - Hey Jarvis". It is the id of the wake word while LVA runs, the name in the wake word selection
    in Home Assistant and the phrase sent to Home Assistant when the wake word is detected. Home Assistant compares the last two, so they must be the same.
    :param origin: Name of the origin directory (internal, downloaded or custom). Other values are left out of the name.
    :param wake_word_type: Engine of the wake word.
    :param wake_word: Phrase of the wake word as written in its configuration.
    :return: The name. It is unique as long as origin, engine and phrase are.
    """
    engine_label = "OWW" if wake_word_type == WakeWordType.OPEN_WAKE_WORD else "MWW"
    parts = [_ORIGIN_LABELS.get(origin), engine_label, wake_word]
    return " - ".join(part for part in parts if part)


def wake_word_ids_for_selections(selections: List[Union[WakeWordSelection, str, None]], available_wake_words: Dict[str, AvailableWakeWord]) -> List[Optional[str]]:
    """
    Turns the wake words stored in the preferences into the ids they have while LVA runs.
    :param selections: Wake words from the preferences in slot order. A plain string is a value of an older version and is kept as it is.
    :param available_wake_words: Dictionary with all available wake words.
    :return: Ids in the same order. A wake word that is not available anymore is None.
    """
    ids_by_selection = {wake_word.selection: wake_word_id for wake_word_id, wake_word in available_wake_words.items()}
    wake_word_ids: List[Optional[str]] = []
    for selection in selections:
        if isinstance(selection, WakeWordSelection):
            wake_word_ids.append(ids_by_selection.get(selection))
        else:
            wake_word_ids.append(selection)

    return wake_word_ids


def wake_word_selections_for_ids(wake_word_ids: List[Optional[str]], available_wake_words: Dict[str, AvailableWakeWord]) -> List[Union[WakeWordSelection, str, None]]:
    """
    Turns the ids of the wake words in the slots into what is stored in the preferences.
    :param wake_word_ids: Wake word ids in slot order.
    :param available_wake_words: Dictionary with all available wake words.
    :return: Entries for the preferences in the same order. An id that is not available is None.
    """
    return [available_wake_words[wake_word_id].selection if wake_word_id in available_wake_words else None for wake_word_id in wake_word_ids]


def find_matching_wake_word_id(
    available_wake_words: Dict[str, AvailableWakeWord],
    requested_id: str,
) -> Optional[str]:
    """
    Finds the id of the wake word a requested value stands for.

    Tries, in order:
        1. An exact id match.
        2. A model with this file name, such as "hey_jarvis". If several origins have it, the one searched last (custom) wins.
        3. A model whose file name starts with "{requested_id}_", which covers openWakeWord's
           versioned filenames (e.g. "hey_jarvis" -> "hey_jarvis_v0.1"). The same rule applies if there are several.
    :param available_wake_words: Dictionary with all available wake words.
    :param requested_id: Id or file name (or its base name) to look for.
    :return: The matching id, or None if no match was found.
    """
    if requested_id in available_wake_words:
        return requested_id

    exact = [key for key, wake_word in available_wake_words.items() if wake_word.file_id == requested_id]
    if exact:
        return exact[-1]

    prefix = f"{requested_id}_"
    prefixed = [key for key, wake_word in available_wake_words.items() if wake_word.file_id.startswith(prefix)]
    return prefixed[-1] if prefixed else None


def find_available_wake_words(wake_word_dirs: List[Path], stop_model_id: str) -> Dict[str, AvailableWakeWord]:
    """
    Searches all available wake words in the specified directories.
    Loads configurations and creates AvailableWakeWord objects.
    :param wake_word_dirs: List of directories to search for wake words. The name of the parent directory is stored as origin of the wake words.
    :param stop_model_id: ID of the stop model which will not be listed as available wake word.
    :return: Dictionary with the id of the wake word (its display name) as key and AvailableWakeWord object as value.
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

                origin = wake_word_dir.parent.name
                wake_word_id = wake_word_display_name(origin, model_type, model_config["wake_word"])
                if wake_word_id in available_wake_words:
                    _LOGGER.warning("Wake word '%s' from %s replaces the one from %s, they have the same name", wake_word_id, model_config_path, available_wake_words[wake_word_id].wake_word_path)

                available_wake_words[wake_word_id] = AvailableWakeWord(
                    id=wake_word_id,
                    type=WakeWordType(model_type),
                    wake_word=model_config["wake_word"],
                    trained_languages=model_config.get("trained_languages", []),
                    wake_word_path=wake_word_path,
                    probability_cutoff=type_config.get("probability_cutoff", 0.7),
                    origin=origin,
                    file_id=model_id,
                )
                _LOGGER.debug("Successfully registered wake word: %s", wake_word_id)

    _LOGGER.debug("Total available wake words found: %d", len(available_wake_words))
    _LOGGER.debug("Available wake words: %s", list(sorted(available_wake_words.keys())))
    return available_wake_words


def load_wake_models(
    available_wake_words: Dict[str, AvailableWakeWord],
    active_wake_word_ids: Optional[List[str]],
    first_default_wake_word_id: str,
    second_default_wake_word_id: str = "",
) -> tuple[Dict[str, Union[MicroWakeWord, OpenWakeWord]], List[str], bool]:
    """
    Loads the specified wake word models.

    If no active wake words are provided, the default model will be loaded, followed by the optional second default model.
    :param available_wake_words: Dictionary with all available wake words.
    :param active_wake_word_ids: List of IDs of wake words to load (may be None).
    :param first_default_wake_word_id: ID of the default model which is loaded if no others are specified.
    :param second_default_wake_word_id: ID (or base name) of the model for the second slot, loaded together with the default model. Empty means none.
        It is skipped with a log message if it is unknown, fails to load or equals the first default model.
    :return: Tuple with (dictionary of loaded models, active wake word IDs in slot order, whether the default fallback was used).
    """
    active_wake_words: List[str] = []
    wake_models: Dict[str, Union[MicroWakeWord, OpenWakeWord]] = {}

    _LOGGER.debug("Requested active wake word ids: %s", active_wake_word_ids)
    _LOGGER.debug("Default wake word id: %s", first_default_wake_word_id)

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
                active_wake_words.append(wake_word_id)
                _LOGGER.debug("✅ Successfully loaded wake model: %s", wake_word_id)
            except Exception as ex:
                _LOGGER.error("❌ Failed to load wake model %s: %s", wake_word_id, ex, exc_info=True)

    if not wake_models:
        # No models loaded, fall back to default model
        _LOGGER.debug("No wake models loaded, falling back to default model")
        first_id, first_model = _load_first_default_wake_word(available_wake_words, first_default_wake_word_id)
        wake_models[first_id] = first_model
        active_wake_words.append(first_id)

        second_word = _load_second_default_wake_word(available_wake_words, first_id, second_default_wake_word_id)
        if second_word is not None:
            second_id, second_model = second_word
            wake_models[second_id] = second_model
            active_wake_words.append(second_id)

    _LOGGER.debug("Loaded %d wake models successfully", len(wake_models))
    _LOGGER.debug("Active wake words: %s", active_wake_words)

    fallback_used = not active_wake_word_ids or not wake_models

    return wake_models, active_wake_words, fallback_used


def load_stop_model(wake_word_dirs: List[Path], stop_model_id: str) -> Optional[MicroWakeWord]:
    """
    Loads the stop word model.
    :param wake_word_dirs: List of directories to search for the stop model.
    :param stop_model_id: ID of the stop model.
    :return: Loaded MicroWakeWord object or None if not found.
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


def _load_first_default_wake_word(
    available_wake_words: Dict[str, AvailableWakeWord],
    first_wake_word_id: str,
) -> Tuple[str, Union[MicroWakeWord, OpenWakeWord]]:
    """
    Loads the default wake word for the first slot, falling back to okay_nabu and then to any available wake word.
    :param available_wake_words: Dictionary with all available wake words.
    :param first_wake_word_id: ID (or base name) requested for the first slot.
    :return: Tuple with (resolved id, loaded model).
    """
    wake_word_id = first_wake_word_id
    wake_word = None

    if wake_word is None:
        # Covers exact ids and versioned openWakeWord filenames like "hey_jarvis_v0.1"
        matched_id = find_matching_wake_word_id(available_wake_words, first_wake_word_id)
        if matched_id is not None:
            wake_word_id = matched_id
            wake_word = available_wake_words[wake_word_id]

    if wake_word is None:
        _LOGGER.error("❌ Default wake word '%s' not found!", wake_word_id)

        # Try fallback to 'okay_nabu'
        wake_word_id = "okay_nabu"
        matched_id = find_matching_wake_word_id(available_wake_words, wake_word_id)
        if matched_id is not None:
            wake_word_id = matched_id

        wake_word = available_wake_words.get(wake_word_id)

        if wake_word is None:
            _LOGGER.error("❌ Fallback wake word 'okay_nabu' also not found!")

            # If absolutely nothing works, take first available wake word
            if available_wake_words:
                wake_word_id = next(iter(available_wake_words.keys()))
                wake_word = available_wake_words[wake_word_id]
                _LOGGER.warning("⚠️ Using first available wake word as last resort: %s", wake_word_id)
            else:
                _LOGGER.critical("❌ NO WAKE WORDS FOUND AT ALL! Cannot proceed.")
                raise RuntimeError("No wake word models available in any search directory")

    _LOGGER.debug("Loading default wake model 1: %s", wake_word_id)
    try:
        model = wake_word.load()
        _LOGGER.debug("✅ Successfully loaded default wake model 1: %s", wake_word_id)
    except Exception as ex:
        _LOGGER.critical("❌ Failed to load even fallback wake word %s: %s", wake_word_id, ex, exc_info=True)
        raise

    return wake_word_id, model


def _load_second_default_wake_word(
    available_wake_words: Dict[str, AvailableWakeWord],
    first_wake_word_id: str,
    second_wake_word_id: str,
) -> Optional[Tuple[str, Union[MicroWakeWord, OpenWakeWord]]]:
    """
    Loads the default wake word for the second slot.
    :param available_wake_words: Dictionary with all available wake words.
    :param first_wake_word_id: ID of the default wake word already loaded for the first slot.
    :param second_wake_word_id: ID (or base name) requested for the second slot. Empty means no second wake word.
    :return: Tuple with (resolved id, loaded model), or None if there is no usable second wake word.
    """
    if not second_wake_word_id:
        return None

    matched_id = find_matching_wake_word_id(available_wake_words, second_wake_word_id)

    if matched_id is None:
        _LOGGER.warning("Second wake word '%s' not found - continuing without it", second_wake_word_id)
        return None

    if matched_id == first_wake_word_id:
        _LOGGER.warning("Second wake word '%s' is the same as the first one - continuing without it", matched_id)
        return None

    try:
        model = available_wake_words[matched_id].load()
    except Exception as ex:
        _LOGGER.error("❌ Failed to load second wake word %s: %s - continuing without it", matched_id, ex, exc_info=True)
        return None

    _LOGGER.debug("✅ Successfully loaded default wake model 2: %s", matched_id)
    return matched_id, model
