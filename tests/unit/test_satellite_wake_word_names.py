"""Tests for the wake word names in linux_voice_assistant/satellite.py (detection phrase, restoring the selection of Home Assistant, reconnect)."""

import asyncio
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

from aioesphomeapi.api_pb2 import (  # type: ignore[attr-defined]  # pylint: disable=no-name-in-module
    VoiceAssistantConfigurationRequest,
    VoiceAssistantConfigurationResponse,
    VoiceAssistantSetConfiguration,
)

from linux_voice_assistant.models import AvailableWakeWord, DetectionResult, Preferences, WakeWordType
from linux_voice_assistant.wake_word_reload import WakeWordListReload
from tests.unit.conftest import make_satellite, make_state
from tests.unit.test_wake_word_download import make_offer

FIRST = AvailableWakeWord("Internal - OWW - Alexa", WakeWordType.OPEN_WAKE_WORD, "Alexa", ["en"], Path("alexa.json"), origin="internal", file_id="alexa_v0.1")
SECOND = AvailableWakeWord("Internal - MWW - Hey Jarvis", WakeWordType.MICRO_WAKE_WORD, "Hey Jarvis", ["en"], Path("hey_jarvis.json"), origin="internal", file_id="hey_jarvis")


def _satellite(tmp_path, step=WakeWordListReload.NONE):
    sat = make_satellite(tmp_path)
    sat.state.available_wake_words = {FIRST.id: FIRST, SECOND.id: SECOND}
    sat.state.wake_words = {FIRST.id: MagicMock(), SECOND.id: MagicMock()}
    sat.state.active_wake_words = {FIRST.id, SECOND.id}
    sat.state.preferences.active_wake_words = [FIRST.selection, SECOND.selection]
    sat.state.wake_word_list_reload = step
    return sat


def _handshake(sat):
    responses = list(sat.handle_message(VoiceAssistantConfigurationRequest()))
    return next(msg for msg in responses if isinstance(msg, VoiceAssistantConfigurationResponse))


class TestDetectionPhrase:
    def test_detection_sends_the_name_of_the_wake_word(self, tmp_path):
        sat = _satellite(tmp_path)

        sat.wakeup(MagicMock(), DetectionResult(wake_word_id=FIRST.id, slot=0))

        assert sat._wake_word_phrase == "Alexa (OWW)"


class TestHandshake:
    def test_reports_both_wake_words_in_slot_order(self, tmp_path):
        response = _handshake(_satellite(tmp_path))

        assert list(response.active_wake_words) == [FIRST.id, SECOND.id]
        assert {ww.id: ww.wake_word for ww in response.available_wake_words} == {FIRST.id: "Alexa (OWW)", SECOND.id: "Hey Jarvis"}

    async def test_changed_names_reconnect_after_the_handshake(self, tmp_path):
        sat = _satellite(tmp_path, WakeWordListReload.NAMES_CHANGED)

        with patch.object(asyncio.get_running_loop(), "call_later") as call_later:
            response = _handshake(sat)

        assert list(response.active_wake_words) == [FIRST.id, SECOND.id]
        assert sat.state.wake_word_list_reload == WakeWordListReload.RESTORE_SELECTION
        call_later.assert_called_once_with(1.0, sat._close_connections_for_reload)

    def test_restore_reports_only_the_first_wake_word_and_clears_the_flag(self, tmp_path, caplog):
        sat = _satellite(tmp_path, WakeWordListReload.RESTORE_SELECTION)
        sat.state.preferences.wake_word_names_changed = True

        with caplog.at_level(logging.WARNING):
            response = _handshake(sat)

        assert list(response.active_wake_words) == [FIRST.id]
        assert sat.state.wake_word_list_reload == WakeWordListReload.NONE
        assert sat.state.preferences.wake_word_names_changed is False
        assert Preferences.from_dict(json.loads(sat.state.preferences_path.read_text())).wake_word_names_changed is False
        assert f"Wake word '{SECOND.id}' is still active" in caplog.text

    def test_offer_from_home_assistant_is_listed_as_available(self, tmp_path):
        sat = _satellite(tmp_path)

        response = next(msg for msg in sat.handle_message(VoiceAssistantConfigurationRequest(external_wake_words=[make_offer()])) if isinstance(msg, VoiceAssistantConfigurationResponse))

        assert {ww.id: ww.wake_word for ww in response.available_wake_words}["Download - MWW - Custom"] == "Download from HA - Custom"

    def test_stored_flag_starts_the_restore(self, tmp_path):
        state = make_state(tmp_path, preferences=Preferences(wake_word_names_changed=True))

        assert state.wake_word_list_reload == WakeWordListReload.NAMES_CHANGED


class TestReconnect:
    def test_closing_for_reload_keeps_the_music_playing(self, tmp_path, caplog):
        sat = _satellite(tmp_path)
        sat.close_connection = MagicMock(side_effect=lambda: sat.connection_lost(None))
        sat.state.connections = [sat]

        with caplog.at_level(logging.DEBUG):
            sat._close_connections_for_reload()

        sat.close_connection.assert_called_once_with()
        sat.state.music_player.stop.assert_not_called()
        sat.state.tts_player.stop.assert_called()
        assert "Music keeps playing: LVA closed the connection itself" in caplog.text

    def test_other_disconnects_still_stop_the_music(self, tmp_path):
        sat = _satellite(tmp_path)
        sat.state.connections = [sat]

        sat.connection_lost(None)

        sat.state.music_player.stop.assert_called_once_with()

    async def test_download_marks_changed_names_and_reconnects(self, tmp_path, caplog):
        sat = _satellite(tmp_path)
        sat._loop = asyncio.get_running_loop()
        list(sat.handle_message(VoiceAssistantConfigurationRequest(external_wake_words=[make_offer()])))
        downloaded = AvailableWakeWord("Download - MWW - Custom", WakeWordType.MICRO_WAKE_WORD, "Custom", ["en"], tmp_path / "custom.json", origin="downloaded", file_id="custom")

        with (
            caplog.at_level(logging.INFO),
            patch("linux_voice_assistant.satellite.download_external_wake_word", return_value=downloaded),
            patch.object(AvailableWakeWord, "load", return_value=MagicMock()),
            patch.object(sat._loop, "call_later") as call_later,
        ):
            list(sat.handle_message(VoiceAssistantSetConfiguration(active_wake_words=[downloaded.id])))
            await sat.state.wake_word_download

        assert sat.state.wake_word_list_reload == WakeWordListReload.NAMES_CHANGED
        call_later.assert_called_once_with(1.0, sat._close_connections_for_reload)
        assert "Wake word names changed by the download, Home Assistant reconnects" in caplog.text
