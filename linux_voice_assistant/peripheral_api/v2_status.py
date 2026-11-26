"""Status of the assistant as version 2 of the peripheral API reports it.

Version 2 tells the peripheral what the assistant is, not which event just happened. The status is worked out from the state of
LVA and the messages are the differences to the status sent before. The functions only decide, they send nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .v2_timers import TimerSummary

PipelineState = str

_PIPELINE_STATE_AFTER_EVENT: Dict[str, PipelineState] = {
    "wake_word_detected": "wake_word",
    "listening": "listening",
    "speech_detected": "speech_detected",
    "thinking": "thinking",
    "tts_speaking": "tts",
    "tts_finished": "idle",
    "idle": "idle",
    "pipeline_error": "idle",
}


@dataclass(frozen=True)
class StatusInputs:
    """What the status is worked out from."""

    connected: bool
    muted: bool
    volume: float
    announcing: bool
    pipeline_state: PipelineState
    media_state: str
    timer: TimerSummary


@dataclass(frozen=True)
class V2Status:
    """Everything that version 2 reports."""

    lva_status: str
    state: str
    muted: bool
    volume: int
    timer_status: str
    timer_remaining_time: int
    timer_remaining_percentage: int
    media_player_status: str


def pipeline_state_after(event: str, current: PipelineState) -> PipelineState:
    """
    Follow the voice pipeline through the events of LVA.
    :param event: Name of the event of LVA.
    :param current: The pipeline state before the event (``idle``, ``wake_word``, ``listening``, ``speech_detected``, ``thinking`` or ``tts``).
    :return: The pipeline state after the event. Events that say nothing about the pipeline keep it. Speech is only detected while listening.
    """
    if event == "speech_detected" and current != "listening":
        return current
    return _PIPELINE_STATE_AFTER_EVENT.get(event, current)


def derive_status(inputs: StatusInputs, previous: Optional[V2Status]) -> V2Status:
    """
    Work out the status that version 2 reports.
    :param inputs: The current state of LVA.
    :param previous: The status reported before, None if nothing was reported yet.
    :return: The status. During an announcement the media player status stays as it was, because an announcement starts and
    stops the music player on its own.
    """
    state = "announce" if inputs.announcing and inputs.pipeline_state in ("idle", "tts") else inputs.pipeline_state
    volume = round(inputs.volume * 100)

    if inputs.announcing and previous is not None:
        media_player_status = previous.media_player_status
    elif inputs.media_state == "playing" and volume == 0:
        media_player_status = "silent"
    else:
        media_player_status = inputs.media_state

    return V2Status(
        lva_status="connected" if inputs.connected else "disconnected",
        state=state,
        muted=inputs.muted,
        volume=volume,
        timer_status=inputs.timer.status,
        timer_remaining_time=inputs.timer.remaining_time,
        timer_remaining_percentage=inputs.timer.remaining_percentage,
        media_player_status=media_player_status,
    )


def status_messages(previous: Optional[V2Status], current: V2Status) -> List[Dict[str, Any]]:
    """
    Build the events that tell a peripheral what changed.
    :param previous: The status the peripheral knows, None for a new peripheral, which gets everything.
    :param current: The current status.
    :return: The events for the values that differ, in a fixed order. The timer comes before the state, so a timer that a wake word or a button ended is reported before the new state.
    """
    candidates = [
        ("lva_status", "status", lambda s: s.lva_status),
        ("timer_status", "status", lambda s: s.timer_status),
        ("timer_remaining_time", "seconds", lambda s: s.timer_remaining_time),
        ("timer_remaining_percentage", "percentage", lambda s: s.timer_remaining_percentage),
        ("state", "state", lambda s: s.state),
        ("mute_status", "muted", lambda s: s.muted),
        ("volume_status", "volume", lambda s: s.volume),
        ("media_player_status", "status", lambda s: s.media_player_status),
    ]
    return [{"event": event, "data": {key: get(current)}} for event, key, get in candidates if previous is None or get(previous) != get(current)]
