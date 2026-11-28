"""Wake-word detection steps for the audio loop.

Selects the active wake words in their Home Assistant slot order, resolves each slot's
sensitivity threshold and scores the models for one audio chunk. Slot 0 and 1 are the
"Wake Word 1/2" slots of Home Assistant, including their sensitivity entities.

While a voice pipeline is running a wake word cannot start another one, so the models are
not run during that time (like ESPHome, which stops wake word detection while the voice
assistant is active). They are reset when detection resumes, so no stale window is scored.
"""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Tuple, Union

import numpy as np
from pymicro_wakeword import MicroWakeWord
from pyopen_wakeword import OpenWakeWord, OpenWakeWordFeatures

from .models import DetectionResult
from .wake_word import wake_word_ids_for_selections

if TYPE_CHECKING:
    from .models import ServerState

_LOGGER = logging.getLogger(__name__)

FALLBACK_THRESHOLD = 0.7


@dataclass(frozen=True)
class ActiveWakeWord:
    """A loaded wake word model together with the Home Assistant slot it occupies."""

    id: str
    slot: int
    model: Union[MicroWakeWord, OpenWakeWord]


def wake_words_need_refresh(state: "ServerState", refreshed_before: bool) -> bool:
    """
    Tell whether the audio loop has to select the active wake words again.
    :param state: Server state holding the loaded models and the changed flag.
    :param refreshed_before: Whether the audio loop selected the active wake words at least once.
    :return: True before the first selection and whenever the active wake words changed.

    An empty selection is a valid result, for example when both wake word selects in Home Assistant are set to "No wake word".
    It must not count as "not selected yet", otherwise the audio loop selects again for every audio chunk and pushes entity states to Home Assistant each time.
    """
    return (not refreshed_before) or (state.wake_words_changed and bool(state.wake_words))


def has_active_wake_word(state: "ServerState") -> bool:
    """
    Tell whether at least one loaded wake word is active.
    :param state: Server state holding the loaded models and the active ids.
    :return: False when no wake word is selected, for example when both wake word selects in Home Assistant are set to "No wake word".
    """
    return any(ww_id in state.active_wake_words for ww_id in state.wake_words)


def select_active_wake_words(state: "ServerState") -> List[ActiveWakeWord]:
    """
    Pick the loaded, active wake words and order them by slot.
    :param state: Server state holding the loaded models, the active ids and the stored slot layout.
    :return: Active wake words sorted by slot. A word missing from the stored layout takes the lowest free slot.
    """
    active_ids = [ww_id for ww_id in state.wake_words if ww_id in state.active_wake_words]
    stored_slots = wake_word_ids_for_selections(state.preferences.active_wake_words, state.available_wake_words)

    slots = {ww_id: stored_slots.index(ww_id) for ww_id in active_ids if ww_id in stored_slots}
    taken = set(slots.values())
    free_slots = (slot for slot in range(len(active_ids) + len(taken)) if slot not in taken)
    for ww_id in active_ids:
        if ww_id not in slots:
            slots[ww_id] = next(free_slots)

    selected = [ActiveWakeWord(ww_id, slots[ww_id], state.wake_words[ww_id]) for ww_id in active_ids]
    if not selected:
        _LOGGER.debug("No wake word is active, nothing listens for a wake word until one is selected")

    return sorted(selected, key=lambda word: word.slot)


def apply_sensitivities(state: "ServerState", wake_words: List[ActiveWakeWord]) -> None:
    """
    Set the sensitivity thresholds of the Wake Word 1/2 slots in use.
    Uses the stored sensitivity, or the model's own cutoff when none is stored.
    :param state: Server state whose ``wake_word_1_threshold`` / ``wake_word_2_threshold`` are updated.
    :param wake_words: Active wake words as returned by :func:`select_active_wake_words`.
    """
    for word in wake_words:
        available_word = state.available_wake_words.get(word.id)
        default_threshold = available_word.probability_cutoff if available_word else FALLBACK_THRESHOLD
        _LOGGER.debug("Using default threshold %.3f for wake word '%s' from model config", default_threshold, word.id)

        if word.slot == 0:
            old_value = state.wake_word_1_threshold
            stored = state.preferences.wake_word_1_sensitivity
            state.wake_word_1_threshold = stored if stored is not None else default_threshold
            _LOGGER.debug("Wake Word 1 threshold set to %.3f (was %.3f, preferences: %s)", state.wake_word_1_threshold, old_value, stored)
        elif word.slot == 1:
            old_value = state.wake_word_2_threshold
            stored = state.preferences.wake_word_2_sensitivity
            state.wake_word_2_threshold = stored if stored is not None else default_threshold
            _LOGGER.debug("Wake Word 2 threshold set to %.3f (was %.3f, preferences: %s)", state.wake_word_2_threshold, old_value, stored)


def gate_wake_words(
    wake_words: List[ActiveWakeWord],
    oww_features: Optional[OpenWakeWordFeatures],
    pipeline_active: bool,
    running: bool,
) -> bool:
    """
    Pause the wake word models while a pipeline runs and reset them when detection resumes.
    :param wake_words: Active wake words as returned by :func:`select_active_wake_words`.
    :param oww_features: openWakeWord feature extractor, reset together with the models, or None if unused.
    :param pipeline_active: Whether a voice pipeline is running, so a wake word could not start another one.
    :param running: Whether the models were already reset and fed since detection last resumed.
    :return: The new value for ``running``. When False, the caller must not feed the models.

    The models keep their feature and probability windows between invokes, so they are
    reset on resume instead of scoring audio from before the pause as if it were current.
    A wake word said in the first fraction of a second after the pipeline ended can be missed.
    """
    if pipeline_active:
        if running:
            _LOGGER.debug("Wake word detection paused for %s (pipeline active)", [word.id for word in wake_words])
        return False

    if not running:
        for word in wake_words:
            word.model.reset()
        if oww_features is not None:
            oww_features.reset()
        _LOGGER.debug("Wake word detection resumed, models reset: %s (detection dead zone of about 0.2 s while the windows refill)", [word.id for word in wake_words])

    return True


def process_wake_words(
    state: "ServerState",
    wake_words: List[ActiveWakeWord],
    micro_inputs: List[np.ndarray],
    oww_inputs: List[np.ndarray],
    last_active: Optional[float],
    now: float,
) -> Tuple[List[Tuple[Union[MicroWakeWord, OpenWakeWord], DetectionResult]], Optional[float]]:
    """
    Score every active wake word for one audio chunk and report the detections that may start a pipeline.
    The models always run, even while muted, so their sliding windows stay current.
    :param state: Server state holding the thresholds, the mute flag and the refractory period.
    :param wake_words: Active wake words as returned by :func:`select_active_wake_words`.
    :param micro_inputs: Micro frontend features of the chunk, for microWakeWord models.
    :param oww_inputs: openWakeWord features of the chunk, for openWakeWord models.
    :param last_active: Monotonic time of the last accepted detection, or None if there was none yet.
    :param now: Current monotonic time.
    :return: ``(detections, last_active)``: the accepted detections in slot order, and the new value for ``last_active``.
    """
    detections: List[Tuple[Union[MicroWakeWord, OpenWakeWord], DetectionResult]] = []

    for word in wake_words:
        threshold = _threshold_for_slot(state, word.slot)
        activated = _score(word.model, threshold, micro_inputs, oww_inputs)

        if activated is None:
            continue

        if state.muted:
            _LOGGER.debug("Wake word '%s' ignored (muted, probability %.3f, threshold %.3f)", word.id, activated, threshold)
            continue

        if state.refractory_seconds <= 0 or last_active is None or (now - last_active) > state.refractory_seconds:
            _LOGGER.debug("Wake word '%s' accepted (slot %d, probability %.3f, threshold %.3f)", word.id, word.slot, activated, threshold)
            detections.append((word.model, DetectionResult(word.id, word.slot, activated, threshold)))
            last_active = now
        else:
            _LOGGER.debug(
                "Wake word '%s' blocked by refractory period (probability %.3f, threshold %.3f, %.2f s of %.1f s left)",
                word.id,
                activated,
                threshold,
                state.refractory_seconds - (now - last_active),
                state.refractory_seconds,
            )

    return detections, last_active


def _threshold_for_slot(state: "ServerState", slot: int) -> float:
    if slot == 0:
        return state.wake_word_1_threshold
    if slot == 1:
        return state.wake_word_2_threshold
    return FALLBACK_THRESHOLD


def _score(
    model: Union[MicroWakeWord, OpenWakeWord],
    threshold: float,
    micro_inputs: List[np.ndarray],
    oww_inputs: List[np.ndarray],
) -> Optional[float]:
    """
    Feed one chunk to a model and return its highest probability above the threshold.
    :param model: Wake word model to score.
    :param threshold: Probability a frame must exceed to count as a detection.
    :param micro_inputs: Micro frontend features of the chunk.
    :param oww_inputs: openWakeWord features of the chunk.
    """
    activated: Optional[float] = None

    if isinstance(model, MicroWakeWord):
        model.probability_cutoff = threshold
        for micro_input in micro_inputs:
            prob = model.process_streaming_prob(micro_input)
            if prob is not None and prob > threshold:
                _LOGGER.debug("Wake word '%s' activated (probability %.3f exceeded threshold %.3f)", model.wake_word, prob, threshold)
                activated = prob if activated is None else max(activated, prob)
    elif isinstance(model, OpenWakeWord):
        for oww_input in oww_inputs:
            for prob in model.process_streaming(oww_input):
                if prob > threshold:
                    _LOGGER.debug("Wake word '%s' activated (probability %.3f exceeded threshold %.3f)", model.wake_word, prob, threshold)  # type: ignore[attr-defined]
                    activated = prob if activated is None else max(activated, prob)

    return activated
