"""Tests for the wake word download in linux_voice_assistant/satellite.py (offers in the configuration, download off the event loop)."""

import asyncio
import logging
import threading
from unittest.mock import MagicMock, patch

from aioesphomeapi.api_pb2 import VoiceAssistantConfigurationRequest, VoiceAssistantSetConfiguration  # type: ignore[attr-defined]  # pylint: disable=no-name-in-module

from linux_voice_assistant.models import AvailableWakeWord, WakeWordType
from tests.unit.conftest import make_satellite
from tests.unit.test_wake_word_download import make_offer

OFFER_ID = "Download - MWW - Custom"


def _downloaded(tmp_path):
    return AvailableWakeWord(OFFER_ID, WakeWordType.MICRO_WAKE_WORD, "Custom", ["en"], tmp_path / "custom.json", origin="downloaded", file_id="custom")


def _local(tmp_path):
    return AvailableWakeWord("Internal - MWW - Okay Nabu", WakeWordType.MICRO_WAKE_WORD, "Okay Nabu", ["en"], tmp_path / "okay_nabu.json", origin="internal", file_id="okay_nabu")


def _satellite_with_offer(tmp_path):
    sat = make_satellite(tmp_path)
    sat._loop = asyncio.get_running_loop()
    list(sat.handle_message(VoiceAssistantConfigurationRequest(external_wake_words=[make_offer()])))
    return sat


class _BlockingDownload:
    """Stands in for download_external_wake_word and blocks the worker thread until released."""

    def __init__(self, result):
        self.result = result
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, external_wake_word, directory):
        self.started.set()
        self.release.wait(5)
        return self.result


async def _wait_for(event: threading.Event) -> None:
    await asyncio.to_thread(event.wait, 5)


class TestConfigurationRequestOffers:
    async def test_offers_are_logged_with_every_field_and_a_summary(self, tmp_path, caplog):
        sat = make_satellite(tmp_path)

        with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.satellite"):
            list(sat.handle_message(VoiceAssistantConfigurationRequest(external_wake_words=[make_offer(), make_offer("oww", "Other", model_type="openWakeWord")])))

        assert "External wake word from Home Assistant: id=custom, wake_word='Custom', type=micro" in caplog.text
        assert f"Offered as '{OFFER_ID}', downloaded when chosen" in caplog.text
        assert "Not offered: model type 'openWakeWord' is not supported" in caplog.text
        assert "Home Assistant offers 2 wake words for download, 1 of them usable" in caplog.text

    async def test_offer_already_on_disk_is_logged_as_downloaded(self, tmp_path, caplog):
        sat = make_satellite(tmp_path)
        sat.state.available_wake_words = {OFFER_ID: _downloaded(tmp_path)}

        with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.satellite"):
            list(sat.handle_message(VoiceAssistantConfigurationRequest(external_wake_words=[make_offer()])))

        assert f"Offered as '{OFFER_ID}', already downloaded" in caplog.text


class TestDownloadOffTheEventLoop:
    async def test_selection_is_activated_after_the_download_without_blocking(self, tmp_path):
        sat = _satellite_with_offer(tmp_path)
        download = _BlockingDownload(_downloaded(tmp_path))

        with (
            patch("linux_voice_assistant.satellite.download_external_wake_word", download),
            patch.object(AvailableWakeWord, "load", return_value=MagicMock()),
        ):
            list(sat.handle_message(VoiceAssistantSetConfiguration(active_wake_words=[OFFER_ID])))
            await _wait_for(download.started)

            # The handler returned while the download still runs, and the old selection is still active.
            assert OFFER_ID not in sat.state.active_wake_words
            assert sat.state.wake_word_download is not None

            download.release.set()
            await sat.state.wake_word_download

        assert sat.state.active_wake_words == {OFFER_ID}
        assert sat.state.available_wake_words[OFFER_ID].file_id == "custom"
        assert sat.state.wake_words_changed is True

    async def test_newer_selection_cancels_the_running_download(self, tmp_path, caplog):
        sat = _satellite_with_offer(tmp_path)
        local = _local(tmp_path)
        sat.state.available_wake_words[local.id] = local
        download = _BlockingDownload(_downloaded(tmp_path))

        with (
            caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.satellite"),
            patch("linux_voice_assistant.satellite.download_external_wake_word", download),
            patch.object(AvailableWakeWord, "load", return_value=MagicMock()),
        ):
            list(sat.handle_message(VoiceAssistantSetConfiguration(active_wake_words=[OFFER_ID])))
            first = sat.state.wake_word_download
            await _wait_for(download.started)

            list(sat.handle_message(VoiceAssistantSetConfiguration(active_wake_words=[local.id])))
            download.release.set()
            await asyncio.sleep(0.05)

        assert first is not None and first.cancelled()
        assert sat.state.active_wake_words == {local.id}
        assert "Wake word download cancelled: Home Assistant sent a newer selection" in caplog.text

    async def test_selection_from_a_new_connection_cancels_the_download_of_the_old_one(self, tmp_path):
        old = _satellite_with_offer(tmp_path)
        local = _local(tmp_path)
        old.state.available_wake_words[local.id] = local
        download = _BlockingDownload(_downloaded(tmp_path))

        with (
            patch("linux_voice_assistant.satellite.download_external_wake_word", download),
            patch.object(AvailableWakeWord, "load", return_value=MagicMock()),
        ):
            list(old.handle_message(VoiceAssistantSetConfiguration(active_wake_words=[OFFER_ID])))
            first = old.state.wake_word_download
            await _wait_for(download.started)

            new = make_satellite(tmp_path)
            new.state = old.state
            new._loop = asyncio.get_running_loop()
            list(new.handle_message(VoiceAssistantSetConfiguration(active_wake_words=[local.id])))
            download.release.set()
            await asyncio.sleep(0.05)

        assert first is not None and first.cancelled()
        assert old.state.active_wake_words == {local.id}

    async def test_failed_download_activates_the_rest_and_logs_a_warning(self, tmp_path, caplog):
        sat = _satellite_with_offer(tmp_path)
        local = _local(tmp_path)
        sat.state.available_wake_words[local.id] = local

        with (
            caplog.at_level(logging.WARNING, logger="linux_voice_assistant.satellite"),
            patch("linux_voice_assistant.satellite.download_external_wake_word", return_value=None),
            patch.object(AvailableWakeWord, "load", return_value=MagicMock()),
        ):
            list(sat.handle_message(VoiceAssistantSetConfiguration(active_wake_words=[local.id, OFFER_ID])))
            await sat.state.wake_word_download

        assert sat.state.active_wake_words == {local.id}
        assert "Wake word 'custom' could not be downloaded and stays inactive" in caplog.text

    async def test_unexpected_error_in_the_download_is_logged(self, tmp_path, caplog):
        sat = _satellite_with_offer(tmp_path)

        with (
            caplog.at_level(logging.ERROR, logger="linux_voice_assistant.satellite"),
            patch("linux_voice_assistant.satellite.download_external_wake_word", side_effect=ValueError("broken")),
        ):
            list(sat.handle_message(VoiceAssistantSetConfiguration(active_wake_words=[OFFER_ID])))
            await sat.state.wake_word_download

        assert "Download of wake word 'custom' failed" in caplog.text
        assert sat.state.active_wake_words == set()

    def test_without_a_connection_the_download_is_skipped(self, tmp_path, caplog):
        sat = make_satellite(tmp_path)
        sat._external_wake_words[OFFER_ID] = make_offer()

        with caplog.at_level(logging.WARNING, logger="linux_voice_assistant.satellite"):
            list(sat.handle_message(VoiceAssistantSetConfiguration(active_wake_words=[OFFER_ID])))

        assert sat.state.wake_word_download is None
        assert "cannot be downloaded without a running connection" in caplog.text
