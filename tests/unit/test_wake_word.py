"""Unit tests for wake_word.py — find_available_wake_words, load_wake_models, load_stop_model."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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
        assert "MWW - okay nabu" in result

    def test_skips_stop_model(self, tmp_path):
        write_json(tmp_path / "stop.json", make_micro_json("stop"))
        write_json(tmp_path / "okay_nabu.json", make_micro_json())
        from linux_voice_assistant.wake_word import find_available_wake_words

        result = find_available_wake_words([tmp_path], stop_model_id="stop")
        assert "stop" not in result
        assert "MWW - okay nabu" in result

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
        assert result["MWW - okay nabu"].wake_word_path == tmp_path / "okay_nabu.json"

    def test_oww_wake_word_path_is_model_file(self, tmp_path):
        write_json(tmp_path / "hey_jarvis.json", make_oww_json(model_file="hey_jarvis.tflite"))
        from linux_voice_assistant.wake_word import find_available_wake_words

        result = find_available_wake_words([tmp_path], stop_model_id="stop")
        assert result["OWW - hey jarvis"].wake_word_path == tmp_path / "hey_jarvis.tflite"

    def test_probability_cutoff_loaded_from_config(self, tmp_path):
        write_json(tmp_path / "okay_nabu.json", make_micro_json(probability_cutoff=0.85))
        from linux_voice_assistant.wake_word import find_available_wake_words

        result = find_available_wake_words([tmp_path], stop_model_id="stop")
        assert result["MWW - okay nabu"].probability_cutoff == pytest.approx(0.85)

    def test_default_probability_cutoff_when_missing(self, tmp_path):
        data = {"type": "micro", "wake_word": "okay nabu", "trained_languages": ["en"]}
        write_json(tmp_path / "okay_nabu.json", data)
        from linux_voice_assistant.wake_word import find_available_wake_words

        result = find_available_wake_words([tmp_path], stop_model_id="stop")
        assert result["MWW - okay nabu"].probability_cutoff == pytest.approx(0.7)

    def test_trained_languages_stored(self, tmp_path):
        data = make_micro_json()
        data["trained_languages"] = ["en", "de"]
        write_json(tmp_path / "okay_nabu.json", data)
        from linux_voice_assistant.wake_word import find_available_wake_words

        result = find_available_wake_words([tmp_path], stop_model_id="stop")
        assert result["MWW - okay nabu"].trained_languages == ["en", "de"]

    def test_searches_multiple_directories(self, tmp_path):
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()
        write_json(dir1 / "okay_nabu.json", make_micro_json())
        write_json(dir2 / "hey_jarvis.json", make_oww_json())
        from linux_voice_assistant.wake_word import find_available_wake_words

        result = find_available_wake_words([dir1, dir2], stop_model_id="stop")
        assert "MWW - okay nabu" in result
        assert "OWW - hey jarvis" in result

    def test_wake_word_text_stored(self, tmp_path):
        write_json(tmp_path / "okay_nabu.json", make_micro_json(wake_word="okay nabu"))
        from linux_voice_assistant.wake_word import find_available_wake_words

        result = find_available_wake_words([tmp_path], stop_model_id="stop")
        assert result["MWW - okay nabu"].wake_word == "okay nabu"

    def test_type_stored_correctly(self, tmp_path):
        from linux_voice_assistant.models import WakeWordType

        write_json(tmp_path / "okay_nabu.json", make_micro_json())
        from linux_voice_assistant.wake_word import find_available_wake_words

        result = find_available_wake_words([tmp_path], stop_model_id="stop")
        assert result["MWW - okay nabu"].type == WakeWordType.MICRO_WAKE_WORD

    def test_origin_is_name_of_parent_directory(self, tmp_path):
        from linux_voice_assistant.wake_word import find_available_wake_words

        model_dir = tmp_path / "custom" / "openwakeword"
        model_dir.mkdir(parents=True)
        write_json(model_dir / "hey_jarvis.json", make_oww_json())

        result = find_available_wake_words([model_dir], stop_model_id="stop")
        assert result["Custom - OWW - hey jarvis"].origin == "custom"
        assert result["Custom - OWW - hey jarvis"].file_id == "hey_jarvis"
        assert result["Custom - OWW - hey jarvis"].wake_word == "hey jarvis"

    def test_same_phrase_in_two_origins_is_listed_twice(self, tmp_path):
        from linux_voice_assistant.wake_word import find_available_wake_words

        internal_dir = tmp_path / "internal" / "microwakeword"
        custom_dir = tmp_path / "custom" / "microwakeword"
        internal_dir.mkdir(parents=True)
        custom_dir.mkdir(parents=True)
        write_json(internal_dir / "okay_nabu.json", make_micro_json())
        write_json(custom_dir / "okay_nabu.json", make_micro_json())

        result = find_available_wake_words([internal_dir, custom_dir], stop_model_id="stop")
        assert list(result) == ["Internal - MWW - okay nabu", "Custom - MWW - okay nabu"]

    def test_same_name_in_one_origin_keeps_the_last_with_a_warning(self, tmp_path, caplog):
        from linux_voice_assistant.wake_word import find_available_wake_words

        custom_dir = tmp_path / "custom" / "microwakeword"
        custom_dir.mkdir(parents=True)
        write_json(custom_dir / "first.json", make_micro_json(wake_word="Same"))
        write_json(custom_dir / "second.json", make_micro_json(wake_word="Same"))

        with caplog.at_level("WARNING"):
            result = find_available_wake_words([custom_dir], stop_model_id="stop")

        assert list(result) == ["Custom - MWW - Same"]
        assert "Wake word 'Custom - MWW - Same' from" in caplog.text


def _make_selection_words() -> dict:
    from linux_voice_assistant.models import AvailableWakeWord, WakeWordType

    return {
        "Internal - MWW - Hey Jarvis": AvailableWakeWord("Internal - MWW - Hey Jarvis", WakeWordType.MICRO_WAKE_WORD, "Hey Jarvis", [], Path("a.json"), origin="internal", file_id="hey_jarvis"),
        "Custom - OWW - Hey Jarvis": AvailableWakeWord("Custom - OWW - Hey Jarvis", WakeWordType.OPEN_WAKE_WORD, "Hey Jarvis", [], Path("b.tflite"), origin="custom", file_id="hey_jarvis_v0.1"),
    }


class TestWakeWordSelections:
    def test_selection_has_origin_engine_and_file_name(self):
        from linux_voice_assistant.models import WakeWordSelection

        words = _make_selection_words()
        assert words["Internal - MWW - Hey Jarvis"].selection == WakeWordSelection("internal", "mww", "hey_jarvis")
        assert words["Custom - OWW - Hey Jarvis"].selection == WakeWordSelection("custom", "oww", "hey_jarvis_v0.1")

    def test_ids_are_turned_into_selections_and_back(self):
        from linux_voice_assistant.wake_word import wake_word_ids_for_selections, wake_word_selections_for_ids

        words = _make_selection_words()
        ids = ["Custom - OWW - Hey Jarvis", None]
        selections = wake_word_selections_for_ids(ids, words)
        assert wake_word_ids_for_selections(selections, words) == ids

    def test_unavailable_wake_words_become_none(self):
        from linux_voice_assistant.models import WakeWordSelection
        from linux_voice_assistant.wake_word import wake_word_ids_for_selections, wake_word_selections_for_ids

        words = _make_selection_words()
        assert wake_word_selections_for_ids(["Gone"], words) == [None]
        assert wake_word_ids_for_selections([WakeWordSelection("custom", "mww", "gone")], words) == [None]

    def test_plain_strings_of_older_versions_are_kept(self):
        from linux_voice_assistant.wake_word import wake_word_ids_for_selections

        assert wake_word_ids_for_selections(["hey_jarvis"], _make_selection_words()) == ["hey_jarvis"]


class TestFindMatchingWakeWordId:
    def _available(self):
        words = _make_selection_words()
        extra = _make_available_word("Custom - MWW - Hey Jarvis")
        extra.file_id = "hey_jarvis"
        words["Custom - MWW - Hey Jarvis"] = extra
        return words

    def test_exact_id_wins(self):
        from linux_voice_assistant.wake_word import find_matching_wake_word_id

        assert find_matching_wake_word_id(self._available(), "Internal - MWW - Hey Jarvis") == "Internal - MWW - Hey Jarvis"

    def test_file_name_resolves_to_last_origin(self):
        from linux_voice_assistant.wake_word import find_matching_wake_word_id

        assert find_matching_wake_word_id(self._available(), "hey_jarvis") == "Custom - MWW - Hey Jarvis"

    def test_base_name_resolves_versioned_open_wake_word(self):
        from linux_voice_assistant.wake_word import find_matching_wake_word_id

        available = self._available()
        del available["Internal - MWW - Hey Jarvis"]
        del available["Custom - MWW - Hey Jarvis"]
        assert find_matching_wake_word_id(available, "hey_jarvis") == "Custom - OWW - Hey Jarvis"

    def test_unknown_returns_none(self):
        from linux_voice_assistant.wake_word import find_matching_wake_word_id

        assert find_matching_wake_word_id(self._available(), "nope") is None


class TestWakeWordDisplayName:
    @pytest.mark.parametrize(
        "origin, word_type, expected",
        [
            ("downloaded", "openWakeWord", "Download - OWW - Hey Jarvis"),
            ("internal", "micro", "Internal - MWW - Hey Jarvis"),
            ("custom", "micro", "Custom - MWW - Hey Jarvis"),
            ("unknown", "openWakeWord", "OWW - Hey Jarvis"),
            ("", "micro", "MWW - Hey Jarvis"),
        ],
    )
    def test_builds_origin_engine_and_phrase(self, origin, word_type, expected):
        from linux_voice_assistant.models import WakeWordType
        from linux_voice_assistant.wake_word import wake_word_display_name

        assert wake_word_display_name(origin, WakeWordType(word_type), "Hey Jarvis") == expected


# ---------------------------------------------------------------------------
# load_wake_models()
# ---------------------------------------------------------------------------


def _make_available_word(model_id: str, word_type=None):
    from linux_voice_assistant.models import AvailableWakeWord, WakeWordType

    available = MagicMock(spec=AvailableWakeWord)
    available.id = model_id
    available.file_id = model_id
    available.wake_word = model_id.replace("_", " ")
    available.type = word_type or WakeWordType.MICRO_WAKE_WORD
    available.load.return_value = MagicMock(name=f"model_{model_id}")
    return available


class TestLoadWakeModelsSecondDefault:
    def _load(self, available, second_id, first_id="okay_nabu", **kwargs):
        from linux_voice_assistant.wake_word import load_wake_models

        return load_wake_models(available, [], first_id, second_default_wake_word_id=second_id, **kwargs)

    def test_empty_id_means_no_second_wake_word(self):
        available = {"okay_nabu": _make_available_word("okay_nabu"), "hey_jarvis": _make_available_word("hey_jarvis")}
        models, slots, _ = self._load(available, "")
        assert slots == ["okay_nabu"]
        assert list(models) == ["okay_nabu"]
        available["hey_jarvis"].load.assert_not_called()

    def test_loads_second_wake_word_after_first(self):
        available = {"hey_jarvis": _make_available_word("hey_jarvis"), "okay_nabu": _make_available_word("okay_nabu")}
        models, slots, fallback = self._load(available, "hey_jarvis")
        assert slots == ["okay_nabu", "hey_jarvis"]
        assert set(models) == {"okay_nabu", "hey_jarvis"}
        assert fallback is True

    def test_resolves_versioned_open_wake_word_id(self):
        from linux_voice_assistant.models import WakeWordType

        available = {"okay_nabu": _make_available_word("okay_nabu"), "hey_jarvis_v0.1": _make_available_word("hey_jarvis_v0.1", WakeWordType.OPEN_WAKE_WORD)}
        _, slots, _ = self._load(available, "hey_jarvis")
        assert slots == ["okay_nabu", "hey_jarvis_v0.1"]

    def test_unknown_id_is_skipped_with_warning(self, caplog):
        with caplog.at_level("WARNING"):
            _, slots, _ = self._load({"okay_nabu": _make_available_word("okay_nabu")}, "nope")
        assert slots == ["okay_nabu"]
        assert "Second wake word 'nope' not found" in caplog.text

    def test_same_as_first_is_skipped_with_warning(self, caplog):
        nabu = _make_available_word("okay_nabu")
        with caplog.at_level("WARNING"):
            _, slots, _ = self._load({"okay_nabu": nabu}, "okay_nabu")
        assert slots == ["okay_nabu"]
        assert "same as the first one" in caplog.text
        nabu.load.assert_called_once()

    def test_load_failure_is_skipped_with_error(self, caplog):
        jarvis = _make_available_word("hey_jarvis")
        jarvis.load.side_effect = RuntimeError("broken model")
        with caplog.at_level("ERROR"):
            models, slots, _ = self._load({"okay_nabu": _make_available_word("okay_nabu"), "hey_jarvis": jarvis}, "hey_jarvis")
        assert slots == ["okay_nabu"]
        assert "hey_jarvis" not in models
        assert "Failed to load second wake word hey_jarvis" in caplog.text

    def test_success_is_logged_at_debug(self, caplog):
        with caplog.at_level("DEBUG"):
            self._load({"okay_nabu": _make_available_word("okay_nabu"), "hey_jarvis": _make_available_word("hey_jarvis")}, "hey_jarvis")
        assert "Successfully loaded default wake model 2: hey_jarvis" in caplog.text

    def test_ignored_when_stored_wake_words_are_loaded(self):
        from linux_voice_assistant.wake_word import load_wake_models

        available = {"okay_nabu": _make_available_word("okay_nabu"), "hey_jarvis": _make_available_word("hey_jarvis")}
        models, slots, fallback = load_wake_models(available, ["hey_jarvis"], "okay_nabu", second_default_wake_word_id="okay_nabu")
        assert slots == ["hey_jarvis"]
        assert list(models) == ["hey_jarvis"]
        assert fallback is False


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

        models, active, fallback = load_wake_models({"okay_nabu": available}, ["okay_nabu"], "okay_nabu")
        assert "okay_nabu" in models
        assert "okay_nabu" in active

    def test_falls_back_to_default_when_no_active(self, tmp_path):
        available, _ = self._make_available(tmp_path)
        from linux_voice_assistant.wake_word import load_wake_models

        models, active, fallback = load_wake_models({"okay_nabu": available}, [], "okay_nabu")
        assert "okay_nabu" in models
        assert fallback is True

    def test_fallback_used_is_false_when_requested_loaded(self, tmp_path):
        available, _ = self._make_available(tmp_path)
        from linux_voice_assistant.wake_word import load_wake_models

        _, _, fallback = load_wake_models({"okay_nabu": available}, ["okay_nabu"], "okay_nabu")
        assert fallback is False

    def test_skips_unknown_wake_word_id(self, tmp_path):
        available, _ = self._make_available(tmp_path)
        from linux_voice_assistant.wake_word import load_wake_models

        models, active, _ = load_wake_models({"okay_nabu": available}, ["unknown_word"], "okay_nabu")
        assert "unknown_word" not in models

    def test_falls_back_to_okay_nabu_when_default_missing(self, tmp_path):
        available, _ = self._make_available(tmp_path, "okay_nabu")
        from linux_voice_assistant.wake_word import load_wake_models

        models, active, _ = load_wake_models({"okay_nabu": available}, [], "nonexistent_default")
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

    def test_active_ids_match_loaded_models(self, tmp_path):
        available, _ = self._make_available(tmp_path)
        from linux_voice_assistant.wake_word import load_wake_models

        models, active, _ = load_wake_models({"okay_nabu": available}, ["okay_nabu"], "okay_nabu")
        assert list(models.keys()) == active

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
