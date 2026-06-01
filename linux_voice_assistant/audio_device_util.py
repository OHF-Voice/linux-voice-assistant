"""Utility functions for audio device resolution and listing."""

import logging
from typing import Optional

import sounddevice as sd

_LOGGER = logging.getLogger(__name__)


def find_sounddevice_by_name(mpv_device_name: Optional[str]) -> Optional[int]:
    """Find the best matching sounddevice for an MPV device name.

    Args:
        mpv_device_name: MPV device name (e.g., "pipewire/bluez_output.XX_XX_XX_XX_XX_XX.1")

    Returns:
        sounddevice index, or None to use default device
    """
    if not mpv_device_name:
        return None

    devices = sd.query_devices()

    # Try exact match first
    for i in range(len(devices)):
        dev = devices[i]
        if dev["max_output_channels"] > 0:
            if dev["name"] == mpv_device_name:
                _LOGGER.info(
                    "Found exact sounddevice match for MPV device '%s': [%d] %s",
                    mpv_device_name,
                    i,
                    dev["name"],
                )
                return i

    # Try substring match (find sounddevice whose name is contained in MPV name)
    # This handles cases like:
    #   MPV: "pipewire/bluez_output.XX_XX_XX_XX_XX_XX.1"
    #   sounddevice: "bluez_output.XX_XX_XX_XX_XX_XX.1"
    best_match = None
    best_match_len = 0

    for i in range(len(devices)):
        dev = devices[i]
        if dev["max_output_channels"] > 0:
            sd_name = dev["name"]
            # Check if sounddevice name appears in MPV name
            if sd_name in mpv_device_name:
                if len(sd_name) > best_match_len:
                    best_match = i
                    best_match_len = len(sd_name)

    if best_match is not None:
        _LOGGER.info(
            "Found sounddevice substring match for MPV device '%s': [%d] %s",
            mpv_device_name,
            best_match,
            devices[best_match]["name"],
        )
        return best_match

    # Try reverse: MPV name contained in sounddevice name
    for i in range(len(devices)):
        dev = devices[i]
        if dev["max_output_channels"] > 0:
            sd_name = dev["name"]
            if mpv_device_name in sd_name:
                _LOGGER.info(
                    "Found sounddevice reverse match for MPV device '%s': [%d] %s",
                    mpv_device_name,
                    i,
                    sd_name,
                )
                return i

    # No match found
    _LOGGER.warning(
        "Could not find sounddevice match for MPV device '%s'. "
        "Using default sounddevice. Run with --list-output-devices to see available devices.",
        mpv_device_name,
    )
    return None


def list_output_devices() -> None:
    """List both MPV and sounddevice output devices."""
    from mpv import MPV

    # List MPV devices
    player = MPV()
    print("MPV Output Devices")
    print("=" * 18)
    for speaker in player.audio_device_list:  # type: ignore
        print(f"  {speaker['name']}: {speaker['description']}")

    print()

    # List sounddevice devices
    print("sounddevice Output Devices")
    print("=" * 26)
    devices = sd.query_devices()
    default_output = sd.default.device[1]

    for i in range(len(devices)):
        dev = devices[i]
        if dev["max_output_channels"] > 0:
            default_marker = " (default)" if i == default_output else ""
            print(
                f"  [{i}] {dev['name']}{default_marker}\n"
                f"      Channels: {dev['max_output_channels']}, "
                f"Sample rate: {dev['default_samplerate']} Hz"
            )

    print()
    print("Note: Use --audio-output-device with an MPV device name.")
    print("      SendSpin will automatically find the best matching sounddevice.")
