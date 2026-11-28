"""Tests for linux_voice_assistant/wake_word_names.py."""

import logging
from pathlib import Path

from linux_voice_assistant.models import AvailableWakeWord, WakeWordType
from linux_voice_assistant.wake_word_names import OfferedWakeWord, name_for_wake_word_id, offered_wake_words, replace_hidden_wake_words, wake_word_name
from tests.unit.test_wake_word_download import make_offer

MWW = WakeWordType.MICRO_WAKE_WORD
OWW = WakeWordType.OPEN_WAKE_WORD
_LABELS = {"internal": "Internal", "downloaded": "Download", "custom": "Custom"}


def _word(origin, engine, phrase, file_id="hey_jarvis"):
    wake_word_id = f"{_LABELS[origin]} - {'OWW' if engine == OWW else 'MWW'} - {phrase}"
    return AvailableWakeWord(wake_word_id, engine, phrase, ["en"], Path(f"{file_id}.json"), origin=origin, file_id=file_id)


def _available(*words):
    return {word.id: word for word in words}


def _names(offered):
    return {wake_word.id: wake_word.name for wake_word in offered}


class TestWakeWordName:
    def test_microwakeword_is_named_by_its_phrase(self):
        assert wake_word_name(_word("internal", MWW, "Hey Jarvis")) == "Hey Jarvis"

    def test_openwakeword_gets_the_suffix(self):
        assert wake_word_name(_word("internal", OWW, "Hey Jarvis")) == "Hey Jarvis (OWW)"


class TestNameForWakeWordId:
    def test_known_id_gives_the_name(self):
        word = _word("custom", OWW, "Alexa")
        assert name_for_wake_word_id(word.id, _available(word)) == "Alexa (OWW)"

    def test_unknown_id_is_returned_as_it_is(self):
        assert name_for_wake_word_id("okay_nabu", {}) == "okay_nabu"


class TestOfferedWakeWords:
    def test_both_engines_are_offered_with_unique_names(self, caplog):
        micro = _word("internal", MWW, "Hey Jarvis")
        open_ = _word("internal", OWW, "Hey Jarvis", "hey_jarvis_v0.1")

        with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.wake_word_names"):
            offered = offered_wake_words(_available(micro, open_), {})

        assert _names(offered) == {micro.id: "Hey Jarvis", open_.id: "Hey Jarvis (OWW)"}
        assert f"Wake word '{open_.id}' offered to Home Assistant as 'Hey Jarvis (OWW)' (openWakeWord gets the suffix ' (OWW)')" in caplog.text
        assert offered[0] == OfferedWakeWord(id=micro.id, name="Hey Jarvis", trained_languages=("en",))

    def test_custom_wins_over_downloaded_and_internal(self, caplog):
        internal = _word("internal", MWW, "Hey Jarvis")
        custom = _word("custom", MWW, "Hey Jarvis")
        downloaded = _word("downloaded", MWW, "hey jarvis ")

        with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.wake_word_names"):
            offered = offered_wake_words(_available(internal, custom, downloaded), {})

        assert _names(offered) == {custom.id: "Hey Jarvis"}
        assert f"Wake word '{internal.id}' is not offered: '{custom.id}' has the same phrase and wins (custom > downloaded > internal)" in caplog.text
        assert f"Wake word '{downloaded.id}' is not offered: '{custom.id}' has the same phrase" in caplog.text

    def test_downloaded_wins_over_internal(self):
        internal = _word("internal", OWW, "Alexa")
        downloaded = _word("downloaded", OWW, "Alexa")

        assert _names(offered_wake_words(_available(downloaded, internal), {})) == {downloaded.id: "Alexa (OWW)"}

    def test_microwakeword_wins_a_name_collision_and_logs_an_error(self, caplog):
        open_ = _word("internal", OWW, "Hey Jarvis")
        micro = _word("custom", MWW, "Hey Jarvis (OWW)")

        with caplog.at_level(logging.ERROR, logger="linux_voice_assistant.wake_word_names"):
            offered = offered_wake_words(_available(open_, micro), {})

        assert _names(offered) == {micro.id: "Hey Jarvis (OWW)"}
        assert f"Wake word '{open_.id}' is not offered: the microWakeWord '{micro.id}' has the phrase 'Hey Jarvis (OWW)'" in caplog.text

    def test_offer_from_home_assistant_is_available_until_downloaded(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.wake_word_names"):
            offered = offered_wake_words({}, {"Download - MWW - Custom": make_offer()})

        assert _names(offered) == {"Download - MWW - Custom": "Download from HA - Custom"}
        assert "Wake word 'custom' from Home Assistant offered as 'Download from HA - Custom' (id 'Download - MWW - Custom'), downloaded when chosen" in caplog.text

    def test_offer_already_downloaded_is_left_out(self, caplog):
        downloaded = _word("downloaded", MWW, "Custom", "custom")

        with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.wake_word_names"):
            offered = offered_wake_words(_available(downloaded), {"Download - MWW - Custom": make_offer()})

        assert _names(offered) == {downloaded.id: "Custom"}
        assert f"Wake word 'custom' from Home Assistant not offered as download: already downloaded as '{downloaded.id}'" in caplog.text

    def test_offer_is_matched_by_file_id_not_by_phrase(self):
        internal = _word("internal", MWW, "Custom", "custom")

        offered = offered_wake_words(_available(internal), {"Download - MWW - Custom": make_offer()})

        assert _names(offered) == {internal.id: "Custom", "Download - MWW - Custom": "Download from HA - Custom"}

    def test_two_offers_with_the_same_phrase_are_offered_once(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.wake_word_names"):
            offered = offered_wake_words({}, {"a": make_offer("first", "Custom"), "b": make_offer("second", "custom")})

        assert _names(offered) == {"a": "Download from HA - Custom"}
        assert "Wake word 'second' from Home Assistant not offered: another download has the same phrase" in caplog.text


class TestReplaceHiddenWakeWords:
    def test_hidden_wake_word_is_replaced_by_the_winner(self, caplog):
        internal = _word("internal", MWW, "Hey Jarvis")
        custom = _word("custom", MWW, "Hey Jarvis")

        with caplog.at_level(logging.INFO, logger="linux_voice_assistant.wake_word_names"):
            slots = replace_hidden_wake_words([internal.id, None], _available(internal, custom))

        assert slots == [custom.id, None]
        assert f"Active wake word '{internal.id}' is hidden by a wake word with the same name (custom > downloaded > internal), using '{custom.id}' instead" in caplog.text

    def test_winner_already_in_a_slot_leaves_the_slot_empty(self):
        internal = _word("internal", MWW, "Hey Jarvis")
        custom = _word("custom", MWW, "Hey Jarvis")

        assert replace_hidden_wake_words([custom.id, internal.id], _available(internal, custom)) == [custom.id, None]

    def test_offered_and_unknown_wake_words_are_kept(self):
        micro = _word("internal", MWW, "Hey Jarvis")
        open_ = _word("internal", OWW, "Hey Jarvis")

        assert replace_hidden_wake_words([open_.id, "hey_jarvis"], _available(micro, open_)) == [open_.id, "hey_jarvis"]
