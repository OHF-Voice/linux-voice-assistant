"""
One-time migrations of stored data when a change makes older stored values unusable.

Each migration is a class named after the pull request that introduced it, so it is clear where it comes from.
The numbers of the migrations that already ran are kept in the preferences, so a migration runs only once.
The functions here only compute the result. Saving it is up to the caller.
"""

import logging
from dataclasses import replace
from typing import Dict, List, Sequence, Type, Union

from .models import AvailableWakeWord, Preferences, WakeWordSelection, WakeWordType
from .wake_word import find_matching_wake_word_id

_LOGGER = logging.getLogger(__name__)


class Migration591:
    """
    Pull request 591 (wake word directory layout) stores a wake word in the preferences with its origin, engine and file name,
    and gave the wake words new names. Wake words stored under their former id or a name are replaced by that entry.
    """

    id = 591

    @staticmethod
    def migrate(preferences: Preferences, available_wake_words: Dict[str, AvailableWakeWord]) -> Preferences:
        """
        Replaces stored wake word ids and names by the entries of the wake words they stand for.

        A stored entry that already has origin, engine and file name is kept. An id is the former id of a model ("hey_jarvis"). A name is the phrase of
        a microWakeWord model ("Hey Jarvis"), the phrase of an openWakeWord model with the former suffix ("Hey Jarvis (OWW)") or the name in the selection
        ("Internal - MWW - Hey Jarvis"). Values that match nothing are kept.
        :param preferences: Preferences as loaded from the preferences file.
        :param available_wake_words: Dictionary with all available wake words.
        :return: Preferences with the migrated wake words.
        """
        ids_by_name = Migration591._ids_by_name(available_wake_words)
        migrated: List[Union[WakeWordSelection, str, None]] = []
        for stored in preferences.active_wake_words:
            if not isinstance(stored, str):
                migrated.append(stored)
                continue

            wake_word_id = find_matching_wake_word_id(available_wake_words, stored) or ids_by_name.get(stored.casefold())
            if wake_word_id is None:
                _LOGGER.debug("Stored wake word '%s' is neither an id nor a known name, keeping it", stored)
                migrated.append(stored)
                continue

            _LOGGER.info("Stored wake word '%s' is now stored as '%s'", stored, wake_word_id)
            migrated.append(available_wake_words[wake_word_id].selection)

        return replace(preferences, active_wake_words=migrated)

    @staticmethod
    def _ids_by_name(available_wake_words: Dict[str, AvailableWakeWord]) -> Dict[str, str]:
        """
        Maps the names a wake word was shown with before to its id, in lower case. A wake word found later in the dictionary wins.
        :param available_wake_words: Dictionary with all available wake words.
        """
        ids_by_name: Dict[str, str] = {}
        for wake_word_id, wake_word in available_wake_words.items():
            former_name = f"{wake_word.wake_word} (OWW)" if wake_word.type == WakeWordType.OPEN_WAKE_WORD else wake_word.wake_word
            ids_by_name[former_name.casefold()] = wake_word_id

        return ids_by_name


class Migration644:
    """
    Pull request 644 (plain wake word phrases) renames the wake words in Home Assistant, for example from "Internal - MWW - Hey Jarvis" to "Hey Jarvis".
    The stored wake words do not change. Home Assistant has to be made to restore its wake word selection, see wake_word_reload.py.
    """

    id = 644

    @staticmethod
    def migrate(preferences: Preferences, available_wake_words: Dict[str, AvailableWakeWord]) -> Preferences:  # pylint: disable=unused-argument
        """
        Marks that the names of the wake words changed, if wake words were chosen before.
        :param preferences: Preferences as loaded from the preferences file.
        :param available_wake_words: Dictionary with all available wake words, not used.
        :return: Preferences with `wake_word_names_changed` set if any wake word is stored.
        """
        if not any(stored is not None for stored in preferences.active_wake_words):
            return preferences

        _LOGGER.info("The names of the wake words in Home Assistant changed, its wake word selection is restored on the next connection")
        return replace(preferences, wake_word_names_changed=True)


_MIGRATIONS: Sequence[Type[Union[Migration591, Migration644]]] = (Migration591, Migration644)


def migrate_preferences(preferences: Preferences, available_wake_words: Dict[str, AvailableWakeWord]) -> Preferences:
    """
    Runs the migrations that have not run yet, oldest first. Call it after the available wake words are known and before the wake words are loaded.
    :param preferences: Preferences as loaded from the preferences file.
    :param available_wake_words: Dictionary with all available wake words.
    :return: The migrated preferences, or the same preferences if nothing was left to migrate. They differ from the given ones if the file has to be saved.
    """
    for migration in _MIGRATIONS:
        if migration.id in preferences.applied_migrations:
            continue

        _LOGGER.debug("Running migration %d", migration.id)
        migrated = migration.migrate(preferences, available_wake_words)
        preferences = replace(migrated, applied_migrations=[*migrated.applied_migrations, migration.id])

    return preferences
