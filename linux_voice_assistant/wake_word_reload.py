"""
Makes Home Assistant read the wake word list again after names changed, and keeps its selection.

Home Assistant reads the list of wake words only when it connects. After a rename (the update that introduced plain phrases, or a download that turns
"Download from HA - Hey Jarvis" into "Hey Jarvis") it no longer finds the option it had selected and shows "No wake word". It restores its first wake word select
by id in a later handshake, but only if exactly one wake word is reported active. So after names changed LVA reconnects, and in the following handshake
reconnects once more and reports only the first active wake word. The functions here decide the steps; closing connections is up to the caller.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, List, Optional

_LOGGER = logging.getLogger(__name__)

RECONNECT_DELAY_SECONDS = 1.0


class WakeWordListReload(str, Enum):
    """Step of making Home Assistant read the wake word list again."""

    NONE = "none"
    NAMES_CHANGED = "names_changed"
    RESTORE_SELECTION = "restore_selection"


@dataclass(frozen=True)
class HandshakeAction:
    """What to do in a configuration handshake with Home Assistant."""

    next_step: WakeWordListReload
    reconnect: bool
    report_first_only: bool


def handshake_action(step: WakeWordListReload) -> HandshakeAction:
    """
    Decides what a configuration handshake does in a step and which step follows.
    :param step: The current step.
    :return: The action. With `NAMES_CHANGED` Home Assistant reconnects once more after this handshake; with `RESTORE_SELECTION` only the first active wake word
        is reported, so Home Assistant restores its first select.
    """
    if step == WakeWordListReload.NAMES_CHANGED:
        _LOGGER.debug("Wake word names changed: Home Assistant now has the new list, reconnecting once more to restore its selection")
        return HandshakeAction(next_step=WakeWordListReload.RESTORE_SELECTION, reconnect=True, report_first_only=False)

    if step == WakeWordListReload.RESTORE_SELECTION:
        _LOGGER.debug("Restoring the wake word selection of Home Assistant: reporting only the first active wake word")
        return HandshakeAction(next_step=WakeWordListReload.NONE, reconnect=False, report_first_only=True)

    return HandshakeAction(next_step=WakeWordListReload.NONE, reconnect=False, report_first_only=False)


def reported_active_wake_words(slot_ids: List[Optional[str]], active_ids: Iterable[str], report_first_only: bool) -> List[str]:
    """
    Active wake words reported to Home Assistant in a configuration handshake.
    :param slot_ids: Ids of the active wake words in slot order.
    :param active_ids: Ids of the wake words that are loaded and active.
    :param report_first_only: Report only the first active wake word, see `handshake_action()`.
    :return: The ids, in slot order. With `report_first_only` only the first one; each one left out is logged as a warning.
    """
    active = set(active_ids)
    reported = [wake_word_id for wake_word_id in slot_ids if wake_word_id is not None and wake_word_id in active]
    if not report_first_only:
        return reported

    first_reported = reported[:1]
    for wake_word_id in reported[1:]:
        _LOGGER.warning("Wake word '%s' is still active, but Home Assistant shows 'No wake word' for it after the rename. Choose it again in Home Assistant.", wake_word_id)

    return first_reported
