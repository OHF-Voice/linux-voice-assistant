"""Tests for linux_voice_assistant/wake_word_download.py."""

import hashlib
import io
import logging
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from aioesphomeapi.api_pb2 import VoiceAssistantExternalWakeWord  # type: ignore[attr-defined]  # pylint: disable=no-name-in-module

from linux_voice_assistant.models import AvailableWakeWord, WakeWordType
from linux_voice_assistant.wake_word_download import (
    DOWNLOAD_TIMEOUT_SECONDS,
    describe_external_wake_word,
    download_external_wake_word,
    external_wake_word_id,
    is_supported,
    wake_words_to_download,
)

MODEL_BYTES = b"tflite-model"


def make_offer(file_id="custom", wake_word="Custom", model_type="micro", model_bytes=MODEL_BYTES):
    return VoiceAssistantExternalWakeWord(
        id=file_id,
        wake_word=wake_word,
        trained_languages=["en"],
        model_type=model_type,
        model_size=len(model_bytes),
        model_hash=hashlib.sha256(model_bytes).hexdigest(),
        url=f"http://ha.local:8123/api/esphome/wake_words/{file_id}.json",
    )


def _response(body: bytes, status: int = 200):
    response = MagicMock()
    response.status = status
    response.read = io.BytesIO(body).read
    response.__enter__ = MagicMock(return_value=response)
    response.__exit__ = MagicMock(return_value=False)
    return response


def _available(wake_word_id, tmp_path):
    return AvailableWakeWord(wake_word_id, WakeWordType.MICRO_WAKE_WORD, "Okay Nabu", ["en"], tmp_path / "okay_nabu.json", origin="internal", file_id="okay_nabu")


class TestExternalWakeWordId:
    def test_is_the_name_of_a_downloaded_microwakeword(self):
        assert external_wake_word_id(make_offer(wake_word="Hey Computer")) == "Download - MWW - Hey Computer"


class TestDescribeExternalWakeWord:
    def test_contains_every_field(self):
        text = describe_external_wake_word(make_offer())

        assert "id=custom" in text
        assert "wake_word='Custom'" in text
        assert "type=micro" in text
        assert "languages=['en']" in text
        assert f"size={len(MODEL_BYTES)}" in text
        assert f"sha256={hashlib.sha256(MODEL_BYTES).hexdigest()}" in text
        assert "url=http://ha.local:8123/api/esphome/wake_words/custom.json" in text


class TestIsSupported:
    def test_micro_is_supported(self):
        assert is_supported(make_offer(model_type="micro"))

    def test_other_types_are_not_supported(self):
        assert not is_supported(make_offer(model_type="openWakeWord"))


class TestWakeWordsToDownload:
    def test_offered_wake_word_that_is_not_on_disk_is_downloaded(self):
        offer = make_offer()

        downloads = wake_words_to_download(["Download - MWW - Custom"], {}, {"Download - MWW - Custom": offer})

        assert downloads == [offer]

    def test_wake_word_on_disk_is_not_downloaded(self, tmp_path):
        wake_word = _available("Download - MWW - Custom", tmp_path)

        downloads = wake_words_to_download(["Download - MWW - Custom"], {wake_word.id: wake_word}, {"Download - MWW - Custom": make_offer()})

        assert not downloads

    def test_unknown_and_local_wake_words_need_no_download(self, tmp_path):
        wake_word = _available("Internal - MWW - Okay Nabu", tmp_path)

        downloads = wake_words_to_download(["okay_nabu", "does_not_exist"], {wake_word.id: wake_word}, {})

        assert not downloads

    def test_each_offer_is_downloaded_once(self):
        offer = make_offer()

        downloads = wake_words_to_download(["Download - MWW - Custom", "Download - MWW - Custom"], {}, {"Download - MWW - Custom": offer})

        assert downloads == [offer]


class TestDownloadExternalWakeWord:
    def test_downloads_config_and_model(self, tmp_path):
        directory = tmp_path / "downloaded" / "microwakeword"
        responses = [_response(b'{"type": "micro"}'), _response(MODEL_BYTES)]

        with patch("linux_voice_assistant.wake_word_download.urlopen", side_effect=responses) as mock_urlopen:
            result = download_external_wake_word(make_offer(), directory)

        assert [c.args[0] for c in mock_urlopen.call_args_list] == [
            "http://ha.local:8123/api/esphome/wake_words/custom.json",
            "http://ha.local:8123/api/esphome/wake_words/custom.tflite",
        ]
        assert all(c.kwargs["timeout"] == DOWNLOAD_TIMEOUT_SECONDS for c in mock_urlopen.call_args_list)
        assert (directory / "custom.json").read_bytes() == b'{"type": "micro"}'
        assert (directory / "custom.tflite").read_bytes() == MODEL_BYTES
        assert result is not None
        assert result.id == "Download - MWW - Custom"
        assert result.origin == "downloaded"
        assert result.file_id == "custom"
        assert result.wake_word_path == directory / "custom.json"

    def test_skips_download_when_size_and_hash_match(self, tmp_path, caplog):
        directory = tmp_path / "downloaded" / "microwakeword"
        directory.mkdir(parents=True)
        (directory / "custom.json").write_text("{}")
        (directory / "custom.tflite").write_bytes(MODEL_BYTES)

        with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.wake_word_download"):
            with patch("linux_voice_assistant.wake_word_download.urlopen") as mock_urlopen:
                result = download_external_wake_word(make_offer(), directory)

        mock_urlopen.assert_not_called()
        assert result is not None
        assert result.id == "Download - MWW - Custom"
        assert "matches size 12 and sha256" in caplog.text
        assert "not downloaded again" in caplog.text

    def test_downloads_again_when_hash_differs(self, tmp_path, caplog):
        directory = tmp_path / "downloaded" / "microwakeword"
        directory.mkdir(parents=True)
        (directory / "custom.json").write_text("{}")
        (directory / "custom.tflite").write_bytes(b"old-model!!!")
        responses = [_response(b"{}"), _response(MODEL_BYTES)]

        with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.wake_word_download"):
            with patch("linux_voice_assistant.wake_word_download.urlopen", side_effect=responses):
                result = download_external_wake_word(make_offer(), directory)

        assert result is not None
        assert (directory / "custom.tflite").read_bytes() == MODEL_BYTES
        assert "Home Assistant offers" in caplog.text

    def test_network_error_returns_none_and_logs_warning(self, tmp_path, caplog):
        directory = tmp_path / "downloaded" / "microwakeword"

        with caplog.at_level(logging.WARNING, logger="linux_voice_assistant.wake_word_download"):
            with patch("linux_voice_assistant.wake_word_download.urlopen", side_effect=URLError("timed out")):
                result = download_external_wake_word(make_offer(), directory)

        assert result is None
        assert "Failed to download wake word file http://ha.local:8123/api/esphome/wake_words/custom.json" in caplog.text
        assert not list(directory.iterdir())

    def test_bad_status_returns_none_and_logs_warning(self, tmp_path, caplog):
        directory = tmp_path / "downloaded" / "microwakeword"

        with caplog.at_level(logging.WARNING, logger="linux_voice_assistant.wake_word_download"):
            with patch("linux_voice_assistant.wake_word_download.urlopen", return_value=_response(b"", status=404)):
                result = download_external_wake_word(make_offer(), directory)

        assert result is None
        assert "status 404" in caplog.text

    def test_failed_model_download_leaves_no_partial_file(self, tmp_path):
        directory = tmp_path / "downloaded" / "microwakeword"
        broken = _response(b"")
        broken.read = MagicMock(side_effect=OSError("connection reset"))

        with patch("linux_voice_assistant.wake_word_download.urlopen", side_effect=[_response(b"{}"), broken]):
            result = download_external_wake_word(make_offer(), directory)

        assert result is None
        assert not (directory / "custom.tflite").exists()
        assert not (directory / "custom.tflite.part").exists()
