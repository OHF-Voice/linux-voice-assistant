from __future__ import annotations

import logging
import re
import subprocess

_LOGGER = logging.getLogger(__name__)


def list_pulse_sink_names() -> list[str]:
    try:
        result = subprocess.run(
            ["pactl", "list", "short", "sinks"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except FileNotFoundError:
        _LOGGER.warning("pactl not found; cannot list PulseAudio/PipeWire sinks")
        return []
    except subprocess.SubprocessError:
        _LOGGER.exception("Failed to list PulseAudio/PipeWire sinks")
        return []

    names: list[str] = []

    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue

        name = parts[1].strip()
        if name:
            names.append(name)

    return sorted(set(names))


def pulse_sink_to_mpv_device(sink_name: str | None) -> str | None:
    if not sink_name or sink_name in ("default", "auto"):
        return None

    if sink_name.startswith("pulse/"):
        return sink_name

    return f"pulse/{sink_name}"


def _label_for_raop_sink(sink_name: str) -> str:
    body = sink_name.removeprefix("raop_sink.")

    match = re.search(r"\.local\.((?:\d{1,3}\.){3}\d{1,3})\.\d+$", body)
    ip_addr = match.group(1) if match else None

    if ".local." in body:
        friendly = body.split(".local.", 1)[0]
    else:
        friendly = body

    friendly = friendly.replace("-", " ")

    if friendly.startswith("Sonos "):
        friendly = "Sonos"

    if ip_addr:
        return f"{friendly} ({ip_addr})"

    return friendly


def pulse_sink_name_to_label(sink_name: str | None) -> str:
    if not sink_name or sink_name in ("default", "auto"):
        return "default"

    if sink_name.startswith("pulse/"):
        sink_name = sink_name.removeprefix("pulse/")

    if sink_name.startswith("raop_sink."):
        return _label_for_raop_sink(sink_name)

    return sink_name


def list_pulse_sink_label_map(*extra_sink_names: str | None) -> dict[str, str]:
    label_map: dict[str, str] = {"default": "default"}

    sink_names = list_pulse_sink_names()

    for extra in extra_sink_names:
        if extra and extra not in ("default", "auto"):
            if extra.startswith("pulse/"):
                extra = extra.removeprefix("pulse/")
            sink_names.append(extra)

    for sink_name in sorted(set(sink_names)):
        base_label = pulse_sink_name_to_label(sink_name)
        label = base_label

        if label in label_map and label_map[label] != sink_name:
            label = sink_name

        label_map[label] = sink_name

    return label_map
