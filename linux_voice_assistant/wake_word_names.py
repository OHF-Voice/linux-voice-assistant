"""
Names of the wake words in Home Assistant.

Home Assistant shows the name in its wake word selection, keys that selection by the name, compares it with the phrase LVA sends on a detection and asks
Voice support by it whether a wake word can be verified. Voice support only knows plain phrases like "Hey Jarvis". So microWakeWord, the standard engine,
is named by its plain phrase and openWakeWord by its phrase with " (OWW)". A name must be unique, so within one engine only one wake word per phrase is
offered: custom before downloaded before internal. Wake words Home Assistant offers for download are named "Download from HA - <phrase>" until they are on disk.
The id of a wake word does not change, only the name shown to Home Assistant.
"""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from .models import AvailableWakeWord, WakeWordType

if TYPE_CHECKING:
    from aioesphomeapi.api_pb2 import VoiceAssistantExternalWakeWord  # type: ignore[attr-defined]

_LOGGER = logging.getLogger(__name__)

OPEN_WAKE_WORD_SUFFIX = " (OWW)"
DOWNLOAD_PREFIX = "Download from HA - "

# Higher wins when two wake words of one engine have the same phrase.
_ORIGIN_RANKS = {"internal": 0, "downloaded": 1, "custom": 2}


@dataclass(frozen=True)
class OfferedWakeWord:
    """A wake word as Home Assistant sees it: the id it sends back and the name it shows."""

    id: str
    name: str
    trained_languages: Tuple[str, ...]


def wake_word_name(wake_word: AvailableWakeWord) -> str:
    """Name of a wake word on disk in Home Assistant: the phrase for microWakeWord, the phrase with " (OWW)" for openWakeWord."""
    if wake_word.type == WakeWordType.OPEN_WAKE_WORD:
        return f"{wake_word.wake_word}{OPEN_WAKE_WORD_SUFFIX}"

    return wake_word.wake_word


def name_for_wake_word_id(wake_word_id: str, available_wake_words: Dict[str, AvailableWakeWord]) -> str:
    """
    Name of a wake word as it is sent to Home Assistant with a detection.
    :param wake_word_id: Id of the detected wake word.
    :param available_wake_words: Wake words on disk, by id.
    :return: The name, or the id itself if the wake word is unknown.
    """
    wake_word = available_wake_words.get(wake_word_id)
    return wake_word_name(wake_word) if wake_word is not None else wake_word_id


def offered_wake_words(available_wake_words: Dict[str, AvailableWakeWord], external_wake_words: "Dict[str, VoiceAssistantExternalWakeWord]") -> List[OfferedWakeWord]:
    """
    Picks the wake words Home Assistant is offered and names them. Every decision is logged.
    :param available_wake_words: Wake words on disk, by id.
    :param external_wake_words: Supported wake words Home Assistant offers for download, by the id LVA gives them.
    :return: Offered wake words, each name only once: the wake words on disk first, then those still to download.

    Within one engine, custom wins over downloaded and downloaded over internal. A microWakeWord whose phrase is the name of an openWakeWord
    ("Hey Jarvis (OWW)") wins over it, logged as an error. An offer that is already on disk in `downloaded` (same file id) is left out.
    """
    winners: Dict[str, AvailableWakeWord] = {}
    for wake_word in available_wake_words.values():
        key = _name_key(wake_word_name(wake_word))
        current = winners.get(key)
        if current is None:
            winners[key] = wake_word
        elif _wins(wake_word, current):
            winners[key] = wake_word
            _log_hidden(current, wake_word)
        else:
            _log_hidden(wake_word, current)

    offered = [OfferedWakeWord(id=wake_word.id, name=wake_word_name(wake_word), trained_languages=tuple(wake_word.trained_languages)) for wake_word in winners.values()]
    for wake_word in winners.values():
        suffix_note = " (openWakeWord gets the suffix ' (OWW)')" if wake_word.type == WakeWordType.OPEN_WAKE_WORD else ""
        _LOGGER.debug("Wake word '%s' offered to Home Assistant as '%s'%s", wake_word.id, wake_word_name(wake_word), suffix_note)

    downloaded_file_ids = {wake_word.file_id: wake_word.id for wake_word in available_wake_words.values() if wake_word.origin == "downloaded"}
    offered_names = {_name_key(wake_word.name) for wake_word in offered}
    for wake_word_id, external_wake_word in external_wake_words.items():
        if external_wake_word.id in downloaded_file_ids:
            _LOGGER.debug("Wake word '%s' from Home Assistant not offered as download: already downloaded as '%s'", external_wake_word.id, downloaded_file_ids[external_wake_word.id])
            continue

        name = f"{DOWNLOAD_PREFIX}{external_wake_word.wake_word}"
        if _name_key(name) in offered_names:
            _LOGGER.debug("Wake word '%s' from Home Assistant not offered: another download has the same phrase ('%s')", external_wake_word.id, name)
            continue

        offered_names.add(_name_key(name))
        offered.append(OfferedWakeWord(id=wake_word_id, name=name, trained_languages=tuple(external_wake_word.trained_languages)))
        _LOGGER.debug("Wake word '%s' from Home Assistant offered as '%s' (id '%s'), downloaded when chosen", external_wake_word.id, name, wake_word_id)

    return offered


def replace_hidden_wake_words(slot_ids: List[Optional[str]], available_wake_words: Dict[str, AvailableWakeWord]) -> List[Optional[str]]:
    """
    Replaces active wake words that are not offered to Home Assistant by the offered wake word with the same name.
    :param slot_ids: Ids of the active wake words in slot order. Unknown ids are kept.
    :param available_wake_words: Wake words on disk, by id.
    :return: Ids in the same slot order. A replacement that is already in an earlier slot leaves the slot empty.

    Call it whenever the wake words are loaded, so LVA never listens for a wake word Home Assistant does not know.
    """
    offered_by_name = {_name_key(wake_word.name): wake_word.id for wake_word in offered_wake_words(available_wake_words, {})}
    offered_ids = set(offered_by_name.values())

    new_slot_ids: List[Optional[str]] = []
    for wake_word_id in slot_ids:
        new_id = wake_word_id
        if wake_word_id is not None and wake_word_id in available_wake_words and wake_word_id not in offered_ids:
            new_id = offered_by_name.get(_name_key(wake_word_name(available_wake_words[wake_word_id])))
            if new_id in new_slot_ids:
                new_id = None
            _LOGGER.info("Active wake word '%s' is hidden by a wake word with the same name (custom > downloaded > internal), using '%s' instead", wake_word_id, new_id)

        new_slot_ids.append(new_id)

    return new_slot_ids


def _wins(candidate: AvailableWakeWord, current: AvailableWakeWord) -> bool:
    """Whether a wake word wins over another one with the same name."""
    if candidate.type != current.type:
        return candidate.type == WakeWordType.MICRO_WAKE_WORD

    return _ORIGIN_RANKS.get(candidate.origin, -1) > _ORIGIN_RANKS.get(current.origin, -1)


def _log_hidden(hidden: AvailableWakeWord, winner: AvailableWakeWord) -> None:
    """Logs a wake word that is not offered because another one has the same name."""
    if hidden.type != winner.type:
        _LOGGER.error(
            "Wake word '%s' is not offered: the microWakeWord '%s' has the phrase '%s', which is the name of this openWakeWord. Rename the phrase of one of them.",
            hidden.id,
            winner.id,
            winner.wake_word,
        )
        return

    _LOGGER.debug("Wake word '%s' is not offered: '%s' has the same phrase and wins (custom > downloaded > internal)", hidden.id, winner.id)


def _name_key(name: str) -> str:
    """Compares names case-insensitively and without surrounding spaces."""
    return name.strip().casefold()
