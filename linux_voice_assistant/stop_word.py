"""Stop-word gating for the audio loop.

The stop word is only acted on while a reply, announcement or timer is playing,
which is exactly when its id is in ``ServerState.active_wake_words``. Running the
model at other times costs a full inference per audio frame for a result that is
discarded, which matters on a single-core board.
"""

import logging
from typing import TYPE_CHECKING, List, Tuple

import numpy as np

if TYPE_CHECKING:
    from .models import ServerState

_LOGGER = logging.getLogger(__name__)


def process_stop_word(state: "ServerState", micro_inputs: List[np.ndarray], running: bool) -> Tuple[bool, bool]:
    """
    Run the stop word for one audio chunk.
    :param state: Server state holding the stop word model, its threshold and the active wake word ids.
    :param micro_inputs: Micro frontend features of the current audio chunk.
    :param running: Whether the model was already reset and fed since it became reachable.
    :return: ``(stopped, running)``: whether the stop word was detected, and the new value for ``running``.

    The model keeps its feature and probability windows between invokes, so it is
    reset when it becomes reachable again rather than scoring a stale window as
    if it were current. That is the only user-visible cost: a word said in the
    first fraction of a second of a reply can be missed.
    """
    stop_active = state.stop_word.id in state.active_wake_words
    if stop_active and not running:
        state.stop_word.reset()
    elif not stop_active:
        running = False

    stopped = False
    if stop_active:
        # Apply the configured stop word sensitivity threshold
        state.stop_word.probability_cutoff = state.stop_word_threshold
        for micro_input in micro_inputs:
            prob = state.stop_word.process_streaming_prob(micro_input)
            if prob is not None and prob > state.stop_word_threshold:
                _LOGGER.debug("Stop word '%s' activated (probability %.3f exceeded threshold %.3f)", state.stop_word.wake_word, prob, state.stop_word_threshold)
                stopped = True

    return stopped, stop_active
