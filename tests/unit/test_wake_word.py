"""Unit tests for wake_word.py — find_available_wake_words, load_wake_models, load_stop_model."""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_micro_json(wake_word: str = "okay nabu", probability_cutoff: float = 0.7) -> dict:
    return {
        "type": "micro",
        "wake_word": wake_word,
        "trained_languages": ["en"],
        "micro": {"probability_cutoff": probability_cutoff},
    }


def make_oww_json(wake_word: str = "hey jarvis", model_file: str = "hey_jarvis.tflite") -> dict:
    return {
        "type": "openWakeWord",
        "wake_word": wake_word,
        "trained_languages": ["en"],
        "model": model_file,
        "openWakeWord": {"probability_cutoff": 0.5},
    }


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# find_available_wake_words()
# ---------------------------------------------------------------------------


class TestFindAvailableWakeWords:
    def test_finds_micro_wake_word(self, tmp_path):
        write_json(tmp_path / "okay_nabu.json", make_micro_json())
        from linux_voice_assistant.wake_word import find_available_wake_words
        result = find_available_wake_words([tmp_path], stop_model_id="stop")
        assert "okay_nabu" in result

    def test_skips_stop_model(self, tmp_path):
        write_json(tmp_path / "stop.json", make_micro_json("stop"))
        write_json(tmp_path / "okay_nabu.json", make_micro_json())
        from linux_voice_assistant.wake_word import find_available_wake_words
        result = find_available_wake_words([tmp_path], stop_model_id="stop")
        assert "stop" not in result
        assert "okay_nabu" in result

    def test_returns_empty_for_empty_directory(self, tmp_path):
        from linux_voice_assistant.wake_word import find_available_wake_words
        result = find_available_wake_words([tmp_path], stop_model_id="stop")
        assert result == {}

    def test_returns_empty_for_nonexistent_directory(self, tmp_path):
        from linux_voice_assistant.wake_word import find_available_wake_words
        result = find_available_wake_words([tmp_path / "does_not_exist"], stop_model_id="stop")
        assert result == {}

    def test_micro_wake_word_path_is_config_path(self, tmp_path):
        write_json(tmp_path / "okay_nabu.json", make_micro_json())
        from linux_voice_assistant.wake_word import find_available_wake_words
        result = find_available_wake_words([tmp_path], stop_model_id="stop")
        assert result["okay_nabu"].wake_word_path == tmp_path / "okay_nabu.json"

    def test_oww_wake_word_path_is_model_file(self, tmp_path):
        write_json(tmp_path / "hey_jarvis.json", make_oww_json(model_file="hey_jarvis.tflite"))
        from linux_voice_assistant.wake_word import find_available_wake_words
        result = find_available_wake_words([tmp_path], stop_model_id="stop")
        assert result["hey_jarvis"].wake_word_path == tmp_path / "hey_jarvis.tflite"

    def test_probability_cutoff_loaded_from_config(self, tmp_path):
        write_json(tmp_path / "okay_nabu.json", make_micro_json(probability_cutoff=0.85))
        from linux_voice_assistant.wake_word import find_available_wake_words
        result = find_available_wake_words([tmp_path], stop_model_id="stop")
        assert result["okay_nabu"].probability_cutoff == pytest.approx(0.85)

    def test_default_probability_cutoff_when_missing(self, tmp_path):
        data = {"type": "micro", "wake_word": "okay nabu", "trained_languages": ["en"]}
        write_json(tmp_path / "okay_nabu.json", data)
        from linux_voice_assistant.wake_word import find_available_wake_words
        result = find_available_wake_words([tmp_path], stop_model_id="stop")
        assert result["okay_nabu"].probability_cutoff == pytest.approx(0.7)

    def test_trained_languages_stored(self, tmp_path):
        data = make_micro_json()
        data["trained_languages"] = ["en", "de"]
        write_json(tmp_path / "okay_nabu.json", data)
        from linux_voice_assistant.wake_word import find_available_wake_words
        result = find_available_wake_words([tmp_path], stop_model_id="stop")
        assert result["okay_nabu"].trained_languages == ["en", "de"]

    def test_searches_multiple_directories(self, tmp_path):
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()
        write_json(dir1 / "okay_nabu.json", make_micro_json())
        write_json(dir2 / "hey_jarvis.json", make_oww_json())
        from linux_voice_assistant.wake_word import find_available_wake_words
        result = find_available_wake_words([dir1, dir2], stop_model_id="stop")
        assert "okay_nabu" in result
        assert "hey_jarvis" in result

    def test_wake_word_text_stored(self, tmp_path):
        write_json(tmp_path / "okay_nabu.json", make_micro_json(wake_word="okay nabu"))
        from linux_voice_assistant.wake_word import find_available_wake_words
        result = find_available_wake_words([tmp_path], stop_model_id="stop")
        assert result["okay_nabu"].wake_word == "okay nabu"

    def test_type_stored_correctly(self, tmp_path):
        from linux_voice_assistant.models import WakeWordType
        write_json(tmp_path / "okay_nabu.json", make_micro_json())
        from linux_voice_assistant.wake_word import find_available_wake_words
        result = find_available_wake_words([tmp_path], stop_model_id="stop")
        assert result["okay_nabu"].type == WakeWordType.MICRO_WAKE_WORD


# ---------------------------------------------------------------------------
# load_wake_models()
# ---------------------------------------------------------------------------


class TestLoadWakeModels:
    def _make_available(self, tmp_path, model_id="okay_nabu"):
        from linux_voice_assistant.models import AvailableWakeWord, WakeWordType
        config_path = tmp_path / f"{model_id}.json"
        write_json(config_path, make_micro_json())
        mock_model = MagicMock()
        mock_model.id = model_id
        available = MagicMock(spec=AvailableWakeWord)
        available.id = model_id
        available.type = WakeWordType.MICRO_WAKE_WORD
        available.wake_word = "okay nabu"
        available.trained_languages = ["en"]
        available.wake_word_path = config_path
        available.probability_cutoff = 0.7
        available.load.return_value = mock_model
        return available, mock_model

    def test_loads_requested_wake_word(self, tmp_path):
        available, mock_model = self._make_available(tmp_path)
        from linux_voice_assistant.wake_word import load_wake_models
        models, active, fallback = load_wake_models(
            {"okay_nabu": available}, ["okay_nabu"], "okay_nabu"
        )
        assert "okay_nabu" in models
        assert "okay_nabu" in active

    def test_falls_back_to_default_when_no_active(self, tmp_path):
        available, _ = self._make_available(tmp_path)
        from linux_voice_assistant.wake_word import load_wake_models
        models, active, fallback = load_wake_models(
            {"okay_nabu": available}, [], "okay_nabu"
        )
        assert "okay_nabu" in models
        assert fallback is True

    def test_fallback_used_is_false_when_requested_loaded(self, tmp_path):
        available, _ = self._make_available(tmp_path)
        from linux_voice_assistant.wake_word import load_wake_models
        _, _, fallback = load_wake_models(
            {"okay_nabu": available}, ["okay_nabu"], "okay_nabu"
        )
        assert fallback is False

    def test_skips_unknown_wake_word_id(self, tmp_path):
        available, _ = self._make_available(tmp_path)
        from linux_voice_assistant.wake_word import load_wake_models
        models, active, _ = load_wake_models(
            {"okay_nabu": available}, ["unknown_word"], "okay_nabu"
        )
        assert "unknown_word" not in models

    def test_falls_back_to_okay_nabu_when_default_missing(self, tmp_path):
        available, _ = self._make_available(tmp_path, "okay_nabu")
        from linux_voice_assistant.wake_word import load_wake_models
        models, active, _ = load_wake_models(
            {"okay_nabu": available}, [], "nonexistent_default"
        )
        assert "okay_nabu" in models

    def test_raises_when_no_wake_words_available(self):
        from linux_voice_assistant.wake_word import load_wake_models
        with pytest.raises(RuntimeError, match="No wake word models available"):
            load_wake_models({}, [], "okay_nabu")

    def test_loads_multiple_requested_models(self, tmp_path):
        available1, _ = self._make_available(tmp_path, "okay_nabu")
        available2, _ = self._make_available(tmp_path, "hey_jarvis")
        from linux_voice_assistant.wake_word import load_wake_models
        models, active, _ = load_wake_models(
            {"okay_nabu": available1, "hey_jarvis": available2},
            ["okay_nabu", "hey_jarvis"],
            "okay_nabu",
        )
        assert "okay_nabu" in models
        assert "hey_jarvis" in models

    def test_active_set_matches_loaded_models(self, tmp_path):
        available, _ = self._make_available(tmp_path)
        from linux_voice_assistant.wake_word import load_wake_models
        models, active, _ = load_wake_models(
            {"okay_nabu": available}, ["okay_nabu"], "okay_nabu"
        )
        assert set(models.keys()) == active

    def test_load_called_once_per_model(self, tmp_path):
        available, _ = self._make_available(tmp_path)
        from linux_voice_assistant.wake_word import load_wake_models
        load_wake_models({"okay_nabu": available}, ["okay_nabu"], "okay_nabu")
        available.load.assert_called_once()


# ---------------------------------------------------------------------------
# load_stop_model()
# ---------------------------------------------------------------------------


class TestLoadStopModel:
    def test_returns_model_when_found(self, tmp_path):
        write_json(tmp_path / "stop.json", make_micro_json("stop"))
        mock_model = MagicMock()
        with patch("linux_voice_assistant.wake_word.MicroWakeWord.from_config", return_value=mock_model):
            from linux_voice_assistant.wake_word import load_stop_model
            result = load_stop_model([tmp_path], "stop")
        assert result is mock_model

    def test_returns_none_when_not_found(self, tmp_path):
        from linux_voice_assistant.wake_word import load_stop_model
        result = load_stop_model([tmp_path], "stop")
        assert result is None

    def test_searches_multiple_directories(self, tmp_path):
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()
        write_json(dir2 / "stop.json", make_micro_json("stop"))
        mock_model = MagicMock()
        with patch("linux_voice_assistant.wake_word.MicroWakeWord.from_config", return_value=mock_model):
            from linux_voice_assistant.wake_word import load_stop_model
            result = load_stop_model([dir1, dir2], "stop")
        assert result is mock_model

    def test_returns_none_when_load_fails(self, tmp_path):
        write_json(tmp_path / "stop.json", make_micro_json("stop"))
        with patch("linux_voice_assistant.wake_word.MicroWakeWord.from_config", side_effect=Exception("load error")):
            from linux_voice_assistant.wake_word import load_stop_model
            result = load_stop_model([tmp_path], "stop")
        assert result is None

    def test_stops_at_first_found(self, tmp_path):
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()
        write_json(dir1 / "stop.json", make_micro_json("stop"))
        write_json(dir2 / "stop.json", make_micro_json("stop"))
        mock_model = MagicMock()
        with patch("linux_voice_assistant.wake_word.MicroWakeWord.from_config", return_value=mock_model) as mock_load:
            from linux_voice_assistant.wake_word import load_stop_model
            load_stop_model([dir1, dir2], "stop")
            mock_load.assert_called_once()