"""Unit tests for the wake word selection, thresholds and scoring in wake_word_detection.py."""

from unittest.mock import MagicMock

import numpy as np
from pymicro_wakeword import MicroWakeWord
from pyopen_wakeword import OpenWakeWord

from linux_voice_assistant.wake_word_detection import (
    ActiveWakeWord,
    apply_sensitivities,
    gate_wake_words,
    process_wake_words,
    select_active_wake_words,
)
from tests.unit.conftest import make_state


def make_micro(prob=0.0):
    model = MagicMock(spec=MicroWakeWord)
    model.process_streaming_prob.return_value = prob
    model.wake_word = "micro"
    return model


def make_oww(probs=()):
    model = MagicMock(spec=OpenWakeWord)
    model.process_streaming.return_value = list(probs)
    model.wake_word = "oww"
    return model


def make_inputs(count=2):
    return [np.zeros((4, 1), dtype=np.float32) for _ in range(count)]


def set_words(state, ids, slots=None):
    state.wake_words = {ww_id: make_micro() for ww_id in ids}
    state.active_wake_words = set(ids)
    state.preferences.active_wake_words = slots if slots is not None else list(ids)


class TestSelectActiveWakeWords:
    def test_orders_by_stored_slot_not_by_dict_order(self, tmp_path):
        state = make_state(tmp_path)
        # dict order b, c, but c holds slot 0 (a was removed and c took its place)
        set_words(state, ["b", "c"], slots=["c", "b"])

        words = select_active_wake_words(state)

        assert [(w.id, w.slot) for w in words] == [("c", 0), ("b", 1)]

    def test_single_word_keeps_its_slot(self, tmp_path):
        state = make_state(tmp_path)
        set_words(state, ["b"], slots=[None, "b"])

        words = select_active_wake_words(state)

        assert [(w.id, w.slot) for w in words] == [("b", 1)]

    def test_unknown_word_takes_lowest_free_slot(self, tmp_path):
        state = make_state(tmp_path)
        set_words(state, ["a", "b"], slots=[None, "b"])

        words = select_active_wake_words(state)

        assert [(w.id, w.slot) for w in words] == [("a", 0), ("b", 1)]

    def test_ignores_inactive_models(self, tmp_path):
        state = make_state(tmp_path)
        set_words(state, ["a", "b"])
        state.active_wake_words = {"a"}

        assert [w.id for w in select_active_wake_words(state)] == ["a"]


class TestApplySensitivities:
    def test_uses_model_default_without_stored_sensitivity(self, tmp_path):
        state = make_state(tmp_path)
        state.available_wake_words = {"a": MagicMock(probability_cutoff=0.55)}
        state.preferences.wake_word_1_sensitivity = None

        apply_sensitivities(state, [ActiveWakeWord("a", 0, make_micro())])

        assert state.wake_word_1_threshold == 0.55

    def test_stored_sensitivity_overrides_default(self, tmp_path):
        state = make_state(tmp_path)
        state.available_wake_words = {"a": MagicMock(probability_cutoff=0.55)}
        state.preferences.wake_word_2_sensitivity = 0.9

        apply_sensitivities(state, [ActiveWakeWord("a", 1, make_micro())])

        assert state.wake_word_2_threshold == 0.9
        assert state.wake_word_1_threshold == 0.7

    def test_unknown_model_falls_back_to_default_cutoff(self, tmp_path):
        state = make_state(tmp_path)
        state.preferences.wake_word_1_sensitivity = None

        apply_sensitivities(state, [ActiveWakeWord("a", 0, make_micro())])

        assert state.wake_word_1_threshold == 0.7


class TestProcessWakeWords:
    def test_detects_above_threshold_and_reports_slot(self, tmp_path):
        state = make_state(tmp_path)
        state.wake_word_2_threshold = 0.5
        model = make_micro(0.8)

        detections, last_active = process_wake_words(state, [ActiveWakeWord("b", 1, model)], make_inputs(), [], None, now=10.0)

        assert len(detections) == 1
        assert detections[0][0] is model
        assert detections[0][1].wake_word_id == "b"
        assert detections[0][1].slot == 1
        assert detections[0][1].score == 0.8
        assert last_active == 10.0
        assert model.probability_cutoff == 0.5

    def test_below_threshold_is_not_a_detection(self, tmp_path):
        state = make_state(tmp_path)
        state.wake_word_1_threshold = 0.7

        detections, last_active = process_wake_words(state, [ActiveWakeWord("a", 0, make_micro(0.6))], make_inputs(), [], None, now=1.0)

        assert detections == []
        assert last_active is None

    def test_best_probability_of_the_chunk_is_reported(self, tmp_path):
        state = make_state(tmp_path)
        model = make_micro()
        model.process_streaming_prob.side_effect = [0.8, 0.95, None]

        detections, _ = process_wake_words(state, [ActiveWakeWord("a", 0, model)], make_inputs(3), [], None, now=1.0)

        assert detections[0][1].score == 0.95

    def test_open_wake_word_is_scored_with_its_own_inputs(self, tmp_path):
        state = make_state(tmp_path)
        model = make_oww([0.2, 0.9])

        detections, _ = process_wake_words(state, [ActiveWakeWord("a", 0, model)], make_inputs(), make_inputs(1), None, now=1.0)

        assert detections[0][1].score == 0.9

    def test_muted_runs_models_but_reports_nothing(self, tmp_path):
        state = make_state(tmp_path)
        state.muted = True
        model = make_micro(0.99)

        detections, last_active = process_wake_words(state, [ActiveWakeWord("a", 0, model)], make_inputs(), [], None, now=1.0)

        assert detections == []
        assert last_active is None
        model.process_streaming_prob.assert_called()

    def test_refractory_blocks_second_detection(self, tmp_path):
        state = make_state(tmp_path)
        state.refractory_seconds = 2.0
        words = [ActiveWakeWord("a", 0, make_micro(0.99))]

        detections, last_active = process_wake_words(state, words, make_inputs(), [], 9.0, now=10.0)

        assert detections == []
        assert last_active == 9.0

    def test_refractory_allows_detection_after_period(self, tmp_path):
        state = make_state(tmp_path)
        state.refractory_seconds = 2.0
        words = [ActiveWakeWord("a", 0, make_micro(0.99))]

        detections, last_active = process_wake_words(state, words, make_inputs(), [], 7.0, now=10.0)

        assert len(detections) == 1
        assert last_active == 10.0

    def test_two_words_in_one_chunk_yield_only_the_first(self, tmp_path):
        state = make_state(tmp_path)
        state.refractory_seconds = 2.0
        words = [ActiveWakeWord("a", 0, make_micro(0.99)), ActiveWakeWord("b", 1, make_micro(0.99))]

        detections, _ = process_wake_words(state, words, make_inputs(), [], None, now=1.0)

        assert [d.wake_word_id for _, d in detections] == ["a"]

    def test_slots_beyond_two_use_fallback_threshold(self, tmp_path):
        state = make_state(tmp_path)

        detections, _ = process_wake_words(state, [ActiveWakeWord("a", 2, make_micro(0.69))], make_inputs(), [], None, now=1.0)

        assert detections == []


class TestGateWakeWords:
    def test_pipeline_active_pauses_without_reset(self):
        model = make_micro()
        words = [ActiveWakeWord("a", 0, model)]

        running = gate_wake_words(words, None, pipeline_active=True, running=False)

        assert running is False
        model.reset.assert_not_called()

    def test_resume_resets_models_and_oww_features_once(self):
        model = make_micro()
        features = MagicMock()
        words = [ActiveWakeWord("a", 0, model)]

        running = gate_wake_words(words, features, pipeline_active=False, running=False)
        running = gate_wake_words(words, features, pipeline_active=False, running=running)

        assert running is True
        model.reset.assert_called_once()
        features.reset.assert_called_once()

    def test_each_pause_resume_cycle_resets_again(self):
        model = make_micro()
        words = [ActiveWakeWord("a", 0, model)]

        running = gate_wake_words(words, None, pipeline_active=False, running=False)
        running = gate_wake_words(words, None, pipeline_active=True, running=running)
        running = gate_wake_words(words, None, pipeline_active=False, running=running)

        assert running is True
        assert model.reset.call_count == 2

    def test_refractory_block_is_logged(self, tmp_path, caplog):
        state = make_state(tmp_path)
        state.refractory_seconds = 2.0
        words = [ActiveWakeWord("a", 0, make_micro(0.99))]

        with caplog.at_level("DEBUG", logger="linux_voice_assistant.wake_word_detection"):
            process_wake_words(state, words, make_inputs(), [], 9.0, now=10.0)

        assert "blocked by refractory period" in caplog.text

    def test_muted_detection_is_logged(self, tmp_path, caplog):
        state = make_state(tmp_path)
        state.muted = True

        with caplog.at_level("DEBUG", logger="linux_voice_assistant.wake_word_detection"):
            process_wake_words(state, [ActiveWakeWord("a", 0, make_micro(0.99))], make_inputs(), [], None, now=1.0)

        assert "ignored (muted" in caplog.text

    def test_accepted_detection_is_logged_with_slot(self, tmp_path, caplog):
        state = make_state(tmp_path)

        with caplog.at_level("DEBUG", logger="linux_voice_assistant.wake_word_detection"):
            process_wake_words(state, [ActiveWakeWord("a", 1, make_micro(0.99))], make_inputs(), [], None, now=1.0)

        assert "accepted (slot 1" in caplog.text

    def test_resume_is_logged_with_dead_zone(self, caplog):
        with caplog.at_level("DEBUG", logger="linux_voice_assistant.wake_word_detection"):
            gate_wake_words([ActiveWakeWord("a", 0, make_micro())], None, pipeline_active=False, running=False)

        assert "resumed" in caplog.text

    def test_refractory_zero_never_blocks(self, tmp_path):
        state = make_state(tmp_path)
        words = [ActiveWakeWord("a", 0, make_micro(0.99))]

        detections, last_active = process_wake_words(state, words, make_inputs(), [], 10.0, now=10.0)

        assert state.refractory_seconds == 0.0
        assert len(detections) == 1
        assert last_active == 10.0
