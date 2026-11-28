"""
Wake words that Home Assistant offers for download.

Home Assistant offers the microWakeWord models a user put into its `custom_wake_words` folder with every `VoiceAssistantConfigurationRequest`.
LVA lists them and downloads one when it is chosen. The download is blocking I/O, so the caller runs it in a worker thread and keeps
the asyncio event loop free. The other functions only decide and describe, they do no I/O.
"""

import hashlib
import logging
import posixpath
import shutil
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib.parse import urlparse, urlunparse
from urllib.request import urlopen

from aioesphomeapi.api_pb2 import VoiceAssistantExternalWakeWord  # type: ignore[attr-defined]  # pylint: disable=no-name-in-module

from .models import AvailableWakeWord, WakeWordType
from .wake_word import find_matching_wake_word_id, wake_word_display_name

_LOGGER = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT_SECONDS = 30
SUPPORTED_MODEL_TYPE = "micro"


def external_wake_word_id(external_wake_word: VoiceAssistantExternalWakeWord) -> str:
    """Id under which LVA lists a wake word that Home Assistant offers for download."""
    return wake_word_display_name("downloaded", WakeWordType.MICRO_WAKE_WORD, external_wake_word.wake_word)


def describe_external_wake_word(external_wake_word: VoiceAssistantExternalWakeWord) -> str:
    """Text with every field of an offered wake word, for the log."""
    return (
        f"id={external_wake_word.id}, wake_word='{external_wake_word.wake_word}', type={external_wake_word.model_type}, "
        f"languages={list(external_wake_word.trained_languages)}, size={external_wake_word.model_size}, "
        f"sha256={external_wake_word.model_hash}, url={external_wake_word.url}"
    )


def is_supported(external_wake_word: VoiceAssistantExternalWakeWord) -> bool:
    """Whether LVA can use an offered wake word. Only microWakeWord models are supported."""
    return bool(external_wake_word.model_type == SUPPORTED_MODEL_TYPE)


def wake_words_to_download(
    requested_ids: Iterable[str],
    available_wake_words: Dict[str, AvailableWakeWord],
    external_wake_words: Dict[str, VoiceAssistantExternalWakeWord],
) -> List[VoiceAssistantExternalWakeWord]:
    """
    Offered wake words that a wake word selection of Home Assistant needs and that are not available yet.
    :param requested_ids: Wake word ids from `VoiceAssistantSetConfiguration.active_wake_words`.
    :param available_wake_words: Wake words LVA has on disk, by id.
    :param external_wake_words: Wake words Home Assistant offers for download, by the id LVA listed them with.
    :return: The offered wake words to download, each once, in the order of the selection.
    """
    downloads: List[VoiceAssistantExternalWakeWord] = []
    for requested_id in requested_ids:
        wake_word_id = find_matching_wake_word_id(available_wake_words, requested_id) or requested_id
        if wake_word_id in available_wake_words:
            continue

        external_wake_word = external_wake_words.get(wake_word_id)
        if external_wake_word is not None and external_wake_word not in downloads:
            downloads.append(external_wake_word)

    return downloads


def download_external_wake_word(external_wake_word: VoiceAssistantExternalWakeWord, directory: Path) -> Optional[AvailableWakeWord]:
    """
    Downloads the config and model file of an offered wake word into a directory. Blocking: call it from a worker thread.
    :param external_wake_word: The wake word Home Assistant offers.
    :param directory: Directory for the files (`downloaded/microwakeword`), created if missing.
    :return: The downloaded wake word, or None if a download failed (logged as a warning).

    A config file that exists is kept. A model file whose size and SHA-256 match the offer is not downloaded again.
    """
    directory.mkdir(parents=True, exist_ok=True)
    config_path = directory / f"{external_wake_word.id}.json"
    model_path = directory / f"{external_wake_word.id}.tflite"
    model_url = _model_url(external_wake_word.url, model_path.name)

    should_download_model = _model_differs(model_path, external_wake_word)
    if (not config_path.exists()) or should_download_model:
        if not _download(external_wake_word.url, config_path):
            return None
    else:
        _LOGGER.debug("Wake word config %s exists, not downloaded again", config_path)

    if should_download_model and not _download(model_url, model_path):
        return None

    return AvailableWakeWord(
        id=external_wake_word_id(external_wake_word),
        type=WakeWordType.MICRO_WAKE_WORD,
        wake_word=external_wake_word.wake_word,
        trained_languages=list(external_wake_word.trained_languages),
        wake_word_path=config_path,
        origin="downloaded",
        file_id=external_wake_word.id,
    )


def _model_differs(model_path: Path, external_wake_word: VoiceAssistantExternalWakeWord) -> bool:
    """Whether the model file is missing or differs from the offer in size or SHA-256 (logged)."""
    if not model_path.exists():
        _LOGGER.debug("Wake word model %s not on disk yet", model_path)
        return True

    model_size = model_path.stat().st_size
    if model_size != external_wake_word.model_size:
        _LOGGER.debug("Wake word model %s has size %d, Home Assistant offers size %d", model_path, model_size, external_wake_word.model_size)
        return True

    with open(model_path, "rb") as model_file:
        model_hash = hashlib.file_digest(model_file, "sha256").hexdigest()

    if model_hash != external_wake_word.model_hash:
        _LOGGER.debug("Wake word model %s has sha256 %s, Home Assistant offers %s", model_path, model_hash, external_wake_word.model_hash)
        return True

    _LOGGER.debug("Wake word model %s matches size %d and sha256 %s, not downloaded again", model_path, model_size, model_hash)
    return False


def _model_url(config_url: str, model_file_name: str) -> str:
    """URL of the model file, next to the config file on the server."""
    parsed_url = urlparse(config_url)
    return urlunparse(parsed_url._replace(path=posixpath.join(posixpath.dirname(parsed_url.path), model_file_name)))


def _download(url: str, path: Path) -> bool:
    """
    Downloads one file, returns whether it worked. Failures are logged as a warning.
    :param url: URL of the file.
    :param path: Target path. It is replaced only by a complete download, a failed one leaves no partial file behind.
    """
    _LOGGER.debug("Downloading %s to %s (timeout %d s)", url, path, DOWNLOAD_TIMEOUT_SECONDS)
    partial_path = path.with_name(f"{path.name}.part")
    try:
        with urlopen(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as request:
            if request.status != 200:
                _LOGGER.warning("Failed to download wake word file %s: status %s", url, request.status)
                return False

            with open(partial_path, "wb") as file:
                shutil.copyfileobj(request, file)
        partial_path.replace(path)
    except OSError as err:
        _LOGGER.warning("Failed to download wake word file %s: %s", url, err)
        partial_path.unlink(missing_ok=True)
        return False

    _LOGGER.debug("Downloaded %s to %s (%d bytes)", url, path, path.stat().st_size)
    return True
