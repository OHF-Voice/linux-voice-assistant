"""Utility functions for audio device resolution and listing."""

import logging
from typing import Optional

import soundcard as sc

_LOGGER = logging.getLogger(__name__)


def find_soundcard_by_name(mpv_device_name: Optional[str]) -> Optional[int]:
    """Find the best matching soundcard speaker for an MPV device name.

    Args:
        mpv_device_name: MPV device name (e.g., "pipewire/bluez_output.XX_XX_XX_XX_XX_XX.1")

    Returns:
        soundcard index, or None to use the default speaker
    """
    if not mpv_device_name:
        return None

    devices = list(sc.all_speakers())
    if not devices:
        return None

    for i, device in enumerate(devices):
        name = getattr(device, "name", "")
        if name and name == mpv_device_name:
            _LOGGER.info("Found exact soundcard match for MPV device '%s': [%d] %s", mpv_device_name, i, name)
            return i

    best_match = None
    best_match_len = 0

    for i, device in enumerate(devices):
        name = getattr(device, "name", "")
        if not name:
            continue
        if name in mpv_device_name and len(name) > best_match_len:
            best_match = i
            best_match_len = len(name)

    if best_match is not None:
        best_name = getattr(devices[best_match], "name", "")
        _LOGGER.info("Found soundcard substring match for MPV device '%s': [%d] %s", mpv_device_name, best_match, best_name)
        return best_match

    for i, device in enumerate(devices):
        name = getattr(device, "name", "")
        if not name:
            continue
        if mpv_device_name in name:
            _LOGGER.info("Found soundcard reverse match for MPV device '%s': [%d] %s", mpv_device_name, i, name)
            return i

    _LOGGER.warning(
        "Could not find soundcard match for MPV device '%s'. Using the default speaker instead. Run with --list-output-devices to see available devices.",
        mpv_device_name,
    )
    return None


def find_sounddevice_by_name(mpv_device_name: Optional[str]) -> Optional[int]:
    """Backward-compatible alias for the soundcard-based lookup."""
    return find_soundcard_by_name(mpv_device_name)


def list_output_devices() -> None:
    """List both MPV and soundcard output devices."""
    from mpv import MPV

    player = MPV()
    print("MPV Output Devices")
    print("=" * 18)
    for speaker in player.audio_device_list:  # type: ignore
        print(f"  {speaker['name']}: {speaker['description']}")

    print()

    print("soundcard Output Devices")
    print("=" * 26)
    devices = list(sc.all_speakers())
    default_obj = getattr(sc, "default", None)
    default_speaker = getattr(default_obj, "speaker", None)
    default_name = getattr(default_speaker, "name", None)

    for i, device in enumerate(devices):
        name = getattr(device, "name", "")
        default_marker = " (default)" if default_name and name == default_name else ""
        print(f"  [{i}] {name}{default_marker}")

    print()
    print("Note: Use --audio-output-device with an MPV device name.")
    print("      SendSpin will automatically find the best matching soundcard output.")
