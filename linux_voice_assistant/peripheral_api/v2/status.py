"""Status of the assistant as version 2 of the peripheral API reports it.

Version 2 tells the peripheral what the assistant is, not which event just happened. The status is worked out from the state of
LVA and the messages are the differences to the status sent before. The functions only decide, they send nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .timers import TimerEntry, TimerSummary

PipelineState = str
_NOTHING = object()

_PIPELINE_STATE_AFTER_EVENT: Dict[str, PipelineState] = {
    "wake_word_detected": "wake_word",
    "wake_word_rejected": "wake_word_rejected",
    "listening": "listening",
    "speech_detected": "speech_detected",
    "thinking": "thinking",
    "tts_speaking": "tts",
    "tts_finished": "idle",
    "idle": "idle",
    "pipeline_error": "pipeline_error",
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
    timer_auto_stopped: bool = False


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
    timer_auto_stopped: bool = False
    timers: Tuple[TimerEntry, ...] = ()


def pipeline_state_after(event: str, current: PipelineState) -> PipelineState:
    """
    Follow the voice pipeline through the events of LVA.
    :param event: Name of the event of LVA.
    :param current: The pipeline state before the event (``idle``, ``wake_word``, ``listening``, ``speech_detected``, ``thinking``, ``tts``, ``pipeline_error`` or ``wake_word_rejected``).
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
        timer_auto_stopped=inputs.timer_auto_stopped,
        timers=inputs.timer.timers,
    )


def status_messages(previous: Optional[V2Status], current: V2Status) -> List[Dict[str, Any]]:
    """
    Build the events that tell a peripheral what changed.
    :param previous: The status the peripheral knows, None for a new peripheral, which gets everything.
    :param current: The current status.
    :return: The events for the values that differ, in a fixed order. The timer comes before the state, so a timer that a wake word or a button ended is reported before the new state.
    ``timer_status`` carries ``auto_stopped``: true in the message that reports a ringing timer that stopped on its own.
    """
    candidates = [
        ("lva_status", {"status": current.lva_status}, current.lva_status != _value(previous, "lva_status")),
        ("timer_status", {"status": current.timer_status, "auto_stopped": current.timer_auto_stopped}, current.timer_status != _value(previous, "timer_status")),
        ("timer_remaining_time", {"seconds": current.timer_remaining_time}, current.timer_remaining_time != _value(previous, "timer_remaining_time")),
        (
            "timer_remaining_percentage",
            {"percentage": current.timer_remaining_percentage},
            current.timer_remaining_percentage != _value(previous, "timer_remaining_percentage"),
        ),
        ("timer_full_data_set", {"timers": [_timer_data(timer) for timer in current.timers]}, current.timers != _value(previous, "timers")),
        ("state", {"state": current.state}, current.state != _value(previous, "state")),
        ("mute_status", {"status": "muted" if current.muted else "unmuted"}, current.muted != _value(previous, "muted")),
        ("volume_status", {"volume": current.volume}, current.volume != _value(previous, "volume")),
        ("media_player_status", {"status": current.media_player_status}, current.media_player_status != _value(previous, "media_player_status")),
    ]
    return [{"event": event, "data": data} for event, data, changed in candidates if changed]


def _value(status: Optional[V2Status], name: str) -> Any:
    """The value of a status field, a value that never equals a real one when nothing was reported yet."""
    return _NOTHING if status is None else getattr(status, name)


def _timer_data(timer: TimerEntry) -> Dict[str, Any]:
    return {"id": timer.id, "name": timer.name, "status": timer.status, "total_seconds": timer.total_seconds, "seconds_left": timer.seconds_left, "percentage": timer.percentage}
