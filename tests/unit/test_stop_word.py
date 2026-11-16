"""Unit tests for the stop-word gating in stop_word.py."""

from unittest.mock import MagicMock

import numpy as np
import pytest

from linux_voice_assistant.stop_word import process_stop_word
from tests.unit.conftest import make_state


def make_inputs(count: int = 2):
    return [np.zeros((4, 1), dtype=np.float32) for _ in range(count)]


def activate(state):
    state.active_wake_words = {state.stop_word.id}


def deactivate(state):
    state.active_wake_words = set()


class TestStopWordLifecycle:
    """The inference/reset lifecycle, not only the final cancellation gate."""

    def test_inactive_does_not_infer(self, tmp_path):
        state = make_state(tmp_path)
        state.stop_word.process_streaming.return_value = True
        deactivate(state)

        stopped, running = process_stop_word(state, make_inputs(), running=False)

        assert stopped is False
        assert running is False
        state.stop_word.process_streaming.assert_not_called()
        state.stop_word.reset.assert_not_called()

    def test_activation_resets_once_and_infers(self, tmp_path):
        state = make_state(tmp_path)
        deactivate(state)
        state.stop_word.process_streaming.return_value = False

        activate(state)
        stopped, running = process_stop_word(state, make_inputs(3), running=False)

        assert stopped is False
        assert running is True
        state.stop_word.reset.assert_called_once()
        assert state.stop_word.process_streaming.call_count == 3

    def test_continued_inference_does_not_reset_again(self, tmp_path):
        state = make_state(tmp_path)
        state.stop_word.process_streaming.return_value = False
        activate(state)

        _, running = process_stop_word(state, make_inputs(), running=False)
        _, running = process_stop_word(state, make_inputs(), running=running)
        _, running = process_stop_word(state, make_inputs(), running=running)

        assert running is True
        state.stop_word.reset.assert_called_once()
        assert state.stop_word.process_streaming.call_count == 6

    def test_detection_reports_stopped(self, tmp_path):
        state = make_state(tmp_path)
        state.stop_word.process_streaming.side_effect = [False, True]
        activate(state)

        stopped, running = process_stop_word(state, make_inputs(2), running=True)

        assert stopped is True
        assert running is True

    def test_deactivation_stops_inference(self, tmp_path):
        state = make_state(tmp_path)
        state.stop_word.process_streaming.return_value = True
        activate(state)
        _, running = process_stop_word(state, make_inputs(), running=False)
        calls_while_active = state.stop_word.process_streaming.call_count

        deactivate(state)
        stopped, running = process_stop_word(state, make_inputs(), running=running)

        assert stopped is False
        assert running is False
        assert state.stop_word.process_streaming.call_count == calls_while_active

    def test_reactivation_resets_again(self, tmp_path):
        state = make_state(tmp_path)
        state.stop_word.process_streaming.return_value = False
        activate(state)
        _, running = process_stop_word(state, make_inputs(), running=False)

        deactivate(state)
        _, running = process_stop_word(state, make_inputs(), running=running)

        activate(state)
        _, running = process_stop_word(state, make_inputs(), running=running)

        assert running is True
        assert state.stop_word.reset.call_count == 2

    def test_configured_threshold_is_applied(self, tmp_path):
        state = make_state(tmp_path)
        state.stop_word.process_streaming.return_value = False
        state.stop_word_threshold = 0.7
        activate(state)

        process_stop_word(state, make_inputs(), running=False)

        assert state.stop_word.probability_cutoff == pytest.approx(0.7)

    def test_threshold_untouched_when_inactive(self, tmp_path):
        state = make_state(tmp_path)
        state.stop_word.probability_cutoff = 0.5
        state.stop_word_threshold = 0.7
        deactivate(state)

        process_stop_word(state, make_inputs(), running=False)

        assert state.stop_word.probability_cutoff == pytest.approx(0.5)


class TestStopWordSequence:
    def test_full_cycle(self, tmp_path):
        """inactive → active → active → inactive → active, as one sequence."""
        state = make_state(tmp_path)
        state.stop_word.process_streaming.return_value = False
        state.stop_word.reset = MagicMock(wraps=state.stop_word.reset)
        running = False

        # inactive: nothing runs
        _, running = process_stop_word(state, make_inputs(), running=running)
        assert state.stop_word.reset.call_count == 0
        assert state.stop_word.process_streaming.call_count == 0

        # active: one reset, inference starts
        activate(state)
        _, running = process_stop_word(state, make_inputs(), running=running)
        assert state.stop_word.reset.call_count == 1
        assert state.stop_word.process_streaming.call_count == 2

        # still active: inference continues, no second reset
        _, running = process_stop_word(state, make_inputs(), running=running)
        assert state.stop_word.reset.call_count == 1
        assert state.stop_word.process_streaming.call_count == 4

        # inactive again: nothing runs
        deactivate(state)
        _, running = process_stop_word(state, make_inputs(), running=running)
        assert running is False
        assert state.stop_word.process_streaming.call_count == 4

        # active again: one more reset, inference resumes
        activate(state)
        _, running = process_stop_word(state, make_inputs(), running=running)
        assert running is True
        assert state.stop_word.reset.call_count == 2
        assert state.stop_word.process_streaming.call_count == 6
