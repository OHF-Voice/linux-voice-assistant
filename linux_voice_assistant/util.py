"""Utility methods."""

import json
import logging
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict, Optional

# netifaces lib is from netifaces2
import netifaces

if TYPE_CHECKING:
    from .models import AvailableWakeWord

# Cache for version to avoid repeated file reading
_version_cache: Optional[str] = None
_esphome_version_cache: Optional[str] = None

# Base directory for the LVA installation
_MODULE_DIR = Path(__file__).parent
_REPO_DIR = _MODULE_DIR.parent
_WAKEWORDS_DIR = _REPO_DIR / "wakewords"

_LOGGER = logging.getLogger(__name__)

# Legacy model ID mappings (_v0.1 suffix removed in refactor)
_LEGACY_MODEL_IDS = {
    # openWakeWord legacy IDs
    "hey_jarvis_v0.1": "hey_jarvis",
    "alexa_v0.1": "alexa",
    "hey_mycroft_v0.1": "hey_mycroft",
    "hey_rhasspy_v0.1": "hey_rhasspy",
    "ok_nabu_v0.1": "ok_nabu",
}


def map_legacy_model_id(model_id: str) -> str:
    """
    Map legacy model IDs to canonical IDs.
    
    The refactored wakewords directory removed _v0.1 suffixes from model IDs.
    This function provides backward compatibility for preferences and CLI args
    that still use legacy IDs.
    
    Args:
        model_id: The model ID to check (may be legacy)
    
    Returns:
        The canonical model ID (without _v0.1 suffix)
    """
    return _LEGACY_MODEL_IDS.get(model_id, model_id)



def get_version() -> str:
    """
    Read the version from version.txt file.

    This function reads the content safely without risk of code injection,
    as it only reads raw text and performs no evaluation.

    Returns:
        str:    The version from version.txt or 'unknown' if the file
                does not exist or cannot be read.
    """
    global _version_cache

    if _version_cache is not None:
        return _version_cache

    version_file = Path(__file__).parent.parent / "version.txt"

    try:
        # Sicher lesen: nur Rohtext, keine Evaluierung
        file_version = version_file.read_text(encoding="utf-8").strip()
        _version_cache = file_version if file_version else "unknown"
    except (FileNotFoundError, PermissionError, OSError):
        _version_cache = "unknown"

    return _version_cache


def get_esphome_version() -> str:
    """
    Read the version of the installed aioesphomeapi package.

    This function uses importlib.metadata to safely retrieve the version
    of an installed Python package without executing any code from the
    package itself.

    Returns:
        str:    The version of aioesphomeapi (e.g., '42.7.0'), or 'unknown'
                if the package is not installed or the version cannot be read.
    """
    global _esphome_version_cache

    if _esphome_version_cache is not None:
        return _esphome_version_cache

    try:
        _esphome_version_cache = version("aioesphomeapi")
    except PackageNotFoundError:
        _esphome_version_cache = "unknown"

    return _esphome_version_cache


def call_all(*callables: Optional[Callable[[], None]]) -> None:
    for item in filter(None, callables):
        item()


def get_default_interface():
    """Return the default network interface name, or None if not found."""
    default_gateway = netifaces.default_gateway()

    if not default_gateway:
        print("No default gateway found")
        return None

    # default_gateway is e.g. {InterfaceType.AF_INET: ('192.168.33.1', 'wlp0s20f3')}
    gateway_info = default_gateway.get(netifaces.AF_INET)
    if not gateway_info:
        print("No default IPv4 gateway found")
        return None

    # gateway_info is a tuple: (gateway_ip, interface_name)
    interface_name = gateway_info[1]
    # print(f"Default interface: {interface_name}")
    return interface_name


def get_default_ipv4(interface: str):
    if not interface:
        return None

    addresses = netifaces.ifaddresses(interface)
    ipv4_info = addresses.get(netifaces.AF_INET)  # type: ignore

    if not ipv4_info:
        return None

    return ipv4_info[0]["addr"]


def discover_wake_word_libraries(download_dir: Path) -> list[str]:
    """
    Discover available wake word libraries by scanning subdirectories of the wakewords root.
    
    A library is considered valid if its subdirectory contains at least one valid
    model JSON file that references an existing model file.
    
    Args:
        download_dir: Path to the download directory (for external wake words)
    
    Returns:
        List of valid library directory names (sorted lexicographically)
    """
    if not _WAKEWORDS_DIR.exists():
        _LOGGER.warning("Wake words directory does not exist: %s", _WAKEWORDS_DIR)
        return []

    valid_libraries: list[str] = []

    # List immediate subdirectories, ignoring hidden dirs
    for entry in sorted(_WAKEWORDS_DIR.iterdir()):
        if not entry.is_dir() or entry.name.startswith('.'):
            continue

        library_name = entry.name

        # Try to scan models in this library directory
        # A library is valid if it has at least one valid model
        try:
            models = scan_wake_words_for_library(library_name, download_dir)
            if models:
                valid_libraries.append(library_name)
                _LOGGER.debug("Discovered valid wake word library: %s (%d models)",
                              library_name, len(models))
            else:
                _LOGGER.debug("Wake word library directory '%s' has no valid models, skipping",
                              library_name)
        except Exception as e:
            _LOGGER.warning("Error scanning wake word library '%s': %s", library_name, e)
            continue

    _LOGGER.info("Discovered %d valid wake word libraries: %s",
                 len(valid_libraries), valid_libraries)

    return valid_libraries


def scan_wake_words_for_library(
    library: str,
    download_dir: Path,
    stop_model: str = "stop"
) -> Dict[str, "AvailableWakeWord"]:
    """
    Scan wake word models for the specified library and return available wake words.
    
    Args:
        library: Name of the library subdirectory under wakewords/ (e.g., "microWakeWord", "openWakeWord")
        download_dir: Path to the download directory (for external wake words)
        stop_model: Model ID to exclude from available list (default: "stop")
    
    Returns:
        Dict mapping model_id to AvailableWakeWord
    """
    from .models import AvailableWakeWord, WakeWordType

    # Compute library directory path dynamically from wakewords root
    library_dir = _WAKEWORDS_DIR / library
    wake_word_dirs = [library_dir, download_dir / "external_wake_words"]
    _LOGGER.info("Scanning wake word library directory: %s", library_dir)

    available_wake_words: Dict[str, AvailableWakeWord] = {}

    for wake_word_dir in wake_word_dirs:
        if not wake_word_dir.exists():
            _LOGGER.debug("Wake word directory does not exist: %s", wake_word_dir)
            continue

        for model_config_path in wake_word_dir.glob("*.json"):

            model_id = model_config_path.stem
            if model_id == stop_model:
                # Don't show stop model as an available wake word
                continue

            try:
                with open(model_config_path, "r", encoding="utf-8") as model_config_file:
                    model_config = json.load(model_config_file)
                    model_type = WakeWordType(model_config["type"])
                    if model_type == WakeWordType.OPEN_WAKE_WORD:
                        wake_word_path = model_config_path.parent / model_config["model"]
                    else:
                        wake_word_path = model_config_path

                    available_wake_words[model_id] = AvailableWakeWord(
                        id=model_id,
                        type=WakeWordType(model_type),
                        wake_word=model_config["wake_word"],
                        trained_languages=model_config.get("trained_languages", []),
                        wake_word_path=wake_word_path,
                    )
            except (json.JSONDecodeError, KeyError, OSError) as e:
                _LOGGER.warning("Failed to load wake word config %s: %s", model_config_path, e)
                continue

    _LOGGER.info("Scanned wake words for library '%s': %d models found: %s",
                 library, len(available_wake_words), list(sorted(available_wake_words.keys())))

    return available_wake_words
