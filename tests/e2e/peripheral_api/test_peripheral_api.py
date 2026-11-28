"""The peripheral WebSocket API the way a peripheral uses it, modelled on the example web console (examples/Peripheral web console).

The console connects, registers a light, a button and a button lock, follows the events and sends the commands of its buttons.
These tests do the same over the WebSocket and check the events, what Home Assistant sees of the commands and, for sound,
what the virtual speaker plays. They run against a second LVA instance, because registering entities adds them to the device
and makes LVA reconnect Home Assistant once.
"""

# pylint: disable=redefined-outer-name

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator, Set

import pytest
from aioesphomeapi import MediaPlayerState, VoiceAssistantTimerEventType

from ..audio import FIRST_MUSIC_TONE_HZ, TTS_TONE_HZ, SpeakerRecorder
from ..fake_ha import FakeHomeAssistant, FixtureServer
from ..lva import LvaProcess
from ..peripheral import PeripheralClient
from ..waiting import wait_until

SETTLE_SECONDS = 2.5

#: The registrations the web console sends on connect, see ws.onopen in peripheral_web_console.html
LIGHT_REGISTRATION = {
    "name": "Web Console LEDs",
    "object_id": "web_console_leds",
    "effects": ["Voice Assistant", "Loop", "None"],
    "supports_rgb": True,
    "supports_brightness": True,
}

#: Ports of the LVA instances whose registrations were made, which makes LVA reconnect Home Assistant exactly once
_registered: Set[int] = set()


@dataclass
class PeripheralSession:
    """A fake Home Assistant and a peripheral, both connected to the same LVA."""

    ha: FakeHomeAssistant
    peripheral: PeripheralClient


@pytest.fixture
async def session(peripheral_lva: LvaProcess) -> AsyncIterator[PeripheralSession]:
    ha = FakeHomeAssistant()
    await ha.connect(peripheral_lva.api_port)
    peripheral = PeripheralClient()
    await peripheral.connect(peripheral_lva.peripheral_port)

    if peripheral_lva.api_port not in _registered:
        ha = await _register_like_web_console(peripheral_lva, ha, peripheral)
    await asyncio.sleep(SETTLE_SECONDS)

    yield PeripheralSession(ha, peripheral)

    await peripheral.disconnect()
    await ha.disconnect()


async def test_registrations_add_entities_and_reconnect_home_assistant_once(peripheral_lva: LvaProcess) -> None:
    """Registering a light, a button and a button lock adds their entities and makes LVA reconnect Home Assistant exactly once."""
    ha = FakeHomeAssistant()
    await ha.connect(peripheral_lva.api_port)
    peripheral = PeripheralClient()
    await peripheral.connect(peripheral_lva.peripheral_port)
    entities_before = [entity.name for entity in ha.entities]
    assert "Button Press" not in entities_before

    ha = await _register_like_web_console(peripheral_lva, ha, peripheral)

    names = [entity.name for entity in ha.entities]
    assert names[: len(entities_before)] == entities_before, "registered entities must be appended, existing keys must not change"
    assert names[len(entities_before) :] == ["Web Console LEDs", "Button Press", "Disable button controls"]

    # The console registers again on every connect, that must neither add entities nor reconnect Home Assistant
    await peripheral.command("register_light", LIGHT_REGISTRATION)
    await peripheral.command("register_button")
    await peripheral.command("register_button_lock")
    await asyncio.sleep(3.0)
    assert not ha.connection_lost
    assert [entity.name for entity in ha.entities] == names
    await peripheral.disconnect()
    await ha.disconnect()


@pytest.mark.usefixtures("session")
async def test_snapshot_describes_the_current_state(peripheral_lva: LvaProcess) -> None:
    """A peripheral that connects receives a snapshot of the current state of LVA."""
    other = PeripheralClient()

    await other.connect(peripheral_lva.peripheral_port)

    assert other.snapshot["ha_connected"] is True
    assert other.snapshot["muted"] is False
    assert other.snapshot["button_controls_locked"] is False
    assert 0.0 <= other.snapshot["volume"] <= 1.0
    assert {"last_stt_text", "last_tts_text"} <= set(other.snapshot)
    await other.disconnect()


async def test_invalid_input_is_ignored(session: PeripheralSession) -> None:
    """Invalid JSON, unknown commands and wrong values are ignored and LVA keeps working."""
    peripheral = session.peripheral
    events = peripheral.mark()

    await peripheral.send_raw("this is not json")
    await peripheral.send_raw('{"command": "no_such_command"}')
    await peripheral.command("set_volume", {"volume": "loud"})

    await peripheral.command("mute_mic")
    await peripheral.wait_for("muted", events, where=lambda data: data["muted"] is True)
    await peripheral.command("unmute_mic")
    await peripheral.wait_for("muted", events, where=lambda data: data["muted"] is False)


async def test_mute_commands_reach_home_assistant(session: PeripheralSession) -> None:
    """The mute commands of a peripheral change the mute switch that Home Assistant sees."""
    ha, peripheral = session.ha, session.peripheral
    events = peripheral.mark()
    mute = ha.entity_by_name("Mute")

    await peripheral.command("mute_mic")

    await peripheral.wait_for("muted", events, where=lambda data: data["muted"] is True)
    await wait_until(lambda: mute.key in ha.states and ha.states[mute.key].state is True, description="the mute switch to turn on")

    await peripheral.command("unmute_mic")

    await peripheral.wait_for("muted", events, where=lambda data: data["muted"] is False)
    await wait_until(lambda: ha.states[mute.key].state is False, description="the mute switch to turn off")


async def test_mute_from_home_assistant_is_reported(session: PeripheralSession) -> None:
    """Muting from Home Assistant is reported to the peripheral as an event."""
    ha, peripheral = session.ha, session.peripheral
    events = peripheral.mark()
    mute = ha.entity_by_name("Mute")

    ha.client.switch_command(mute.key, True)
    await peripheral.wait_for("muted", events, where=lambda data: data["muted"] is True)
    other = PeripheralClient()
    await other.connect(session.peripheral.port)
    assert other.snapshot["muted"] is True
    assert "muted" in [entry["event"] for entry in other.events], "a new peripheral must be told the muted state"
    await other.disconnect()

    ha.client.switch_command(mute.key, False)
    await peripheral.wait_for("muted", events, where=lambda data: data["muted"] is False)


async def test_volume_commands_reach_the_media_player(session: PeripheralSession) -> None:
    """The volume commands of a peripheral change the volume of the media player and are reported back."""
    ha, peripheral = session.ha, session.peripheral
    events = peripheral.mark()
    media_player = ha.entity_by_name("Media Player")

    await peripheral.command("set_volume", {"volume": 0.5})
    await peripheral.wait_for("volume_changed", events, where=lambda data: abs(data["volume"] - 0.5) < 0.01)
    await wait_until(lambda: media_player.key in ha.states and abs(ha.states[media_player.key].volume - 0.5) < 0.01, description="the media player volume")

    await peripheral.command("volume_up")
    await peripheral.wait_for("volume_changed", events, where=lambda data: data["volume"] > 0.52)
    await peripheral.command("volume_down")
    await peripheral.wait_for("volume_changed", events, where=lambda data: abs(data["volume"] - 0.5) < 0.01)

    await peripheral.command("set_volume", {"volume": 1.0})
    await peripheral.wait_for("volume_changed", events, where=lambda data: data["volume"] > 0.99)


async def test_start_listening_runs_a_conversation_reported_as_events(session: PeripheralSession, speaker: SpeakerRecorder, media_server: FixtureServer) -> None:
    """The start listening command runs a voice request whose phases the peripheral receives as events."""
    ha, peripheral = session.ha, session.peripheral
    events = peripheral.mark()
    speaker_mark = speaker.mark()

    await peripheral.command("start_listening")

    await peripheral.wait_for("listening", events)
    assert await speaker.wait_for_sound(speaker_mark, timeout=5), "the start listening sound was not played"
    start = await ha.wait_for_start(1, timeout=10)
    assert not start.wake_word_phrase or "Okay Nabu" in start.wake_word_phrase
    ha.run_pipeline("what time is it", "It is noon", f"{media_server.base_url}/tts_marker.wav")

    stt = await peripheral.wait_for("stt_text", events)
    assert stt["data"] == {"text": "what time is it"}
    await peripheral.wait_for("thinking", events)
    tts = await peripheral.wait_for("tts_text", events)
    assert tts["data"] == {"text": "It is noon"}
    await peripheral.wait_for("tts_speaking", events)
    assert await speaker.wait_for_tone(speaker_mark, TTS_TONE_HZ, timeout=10)
    await peripheral.wait_for("tts_finished", events)
    await peripheral.wait_for("idle", events)

    late = PeripheralClient()
    await late.connect(session.peripheral.port)
    assert late.snapshot["last_stt_text"] == "what time is it"
    assert late.snapshot["last_tts_text"] == "It is noon"
    await late.disconnect()


async def test_start_listening_is_ignored_while_muted(session: PeripheralSession) -> None:
    """The start listening command does nothing while LVA is muted."""
    ha, peripheral = session.ha, session.peripheral
    events = peripheral.mark()
    await peripheral.command("mute_mic")
    await peripheral.wait_for("muted", events, where=lambda data: data["muted"] is True)

    await peripheral.command("start_listening")
    await asyncio.sleep(1.5)

    assert not ha.starts
    assert "listening" not in peripheral.names_since(events)
    await peripheral.command("unmute_mic")
    await peripheral.wait_for("muted", events, where=lambda data: data["muted"] is False)


async def test_stop_pipeline_ends_a_spoken_reply(session: PeripheralSession, speaker: SpeakerRecorder, media_server: FixtureServer) -> None:
    """The stop pipeline command cuts off a spoken reply while it plays."""
    ha, peripheral = session.ha, session.peripheral
    events = peripheral.mark()
    await peripheral.command("start_listening")
    await ha.wait_for_start(1, timeout=10)
    ha.run_pipeline("tell me a story", "Once upon a time", f"{media_server.base_url}/tts_marker_long.wav")
    await peripheral.wait_for("tts_speaking", events)
    assert await speaker.wait_for_tone(speaker.mark(), TTS_TONE_HZ, timeout=5)

    await peripheral.command("stop_pipeline")

    await peripheral.wait_for("tts_finished", events, timeout=8)
    await peripheral.wait_for("idle", events)
    await asyncio.sleep(0.5)
    assert not await speaker.wait_for_tone(speaker.mark(), TTS_TONE_HZ, timeout=1.5), "the reply kept playing"


async def test_pipeline_error_is_reported(session: PeripheralSession) -> None:
    """A pipeline error is reported to the peripheral and LVA returns to idle."""
    ha, peripheral = session.ha, session.peripheral
    events = peripheral.mark()
    await peripheral.command("start_listening")
    await ha.wait_for_start(1, timeout=10)

    ha.send_error("stt-no-text-recognized", "No text recognized")
    ha.run_pipeline("", None, None)

    await peripheral.wait_for("pipeline_error", events)
    await peripheral.wait_for("idle", events)


async def test_timer_events_and_stop_timer_ringing(session: PeripheralSession, speaker: SpeakerRecorder) -> None:
    """Timer events reach the peripheral and the stop timer ringing command silences a ringing timer."""
    ha, peripheral = session.ha, session.peripheral
    events = peripheral.mark()

    ha.timer_event(VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_STARTED, "t1", 90, 90)
    ticking = await peripheral.wait_for("timer_ticking", events)
    assert ticking["data"] == {"id": "t1", "name": "e2e timer", "total_seconds": 90, "seconds_left": 90}

    ha.timer_event(VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_UPDATED, "t1", 90, 45)
    updated = await peripheral.wait_for("timer_updated", events)
    assert updated["data"]["seconds_left"] == 45

    late = PeripheralClient()
    await late.connect(session.peripheral.port)
    assert "timer_ticking" in [entry["event"] for entry in late.events], "a new peripheral must get the running countdown replayed"
    await late.disconnect()

    ha.timer_event(VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_FINISHED, "t1", 90, 0)
    await peripheral.wait_for("timer_ringing", events)
    assert await speaker.wait_for_sound(speaker.mark(), timeout=5), "the timer sound was not played"

    await peripheral.command("stop_timer_ringing")

    await peripheral.wait_for("idle", events, timeout=8)
    await asyncio.sleep(2.5)
    assert not await speaker.wait_for_sound(speaker.mark(), timeout=2), "the timer kept ringing"


async def test_timer_that_nobody_stops_ends_on_its_own_and_is_reported(auto_stop_lva: LvaProcess) -> None:
    """A ringing timer that nobody stops ends after --timer-max-ring-seconds. The peripheral gets timer_auto_stopped with how long it rang, then idle."""
    ha = FakeHomeAssistant()
    await ha.connect(auto_stop_lva.api_port)
    peripheral = PeripheralClient()
    await peripheral.connect(auto_stop_lva.peripheral_port)
    await asyncio.sleep(SETTLE_SECONDS)
    events = peripheral.mark()

    try:
        ha.timer_event(VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_FINISHED, "t3", 60, 0)
        await peripheral.wait_for("timer_ringing", events)

        stopped = await peripheral.wait_for("timer_auto_stopped", events, timeout=15)
        await peripheral.wait_for("idle", events)
    finally:
        await peripheral.disconnect()
        await ha.disconnect()

    assert stopped["data"]["seconds"] >= 3, "the event must say how long the timer rang"
    names = peripheral.names_since(events)
    assert "idle" in names[names.index("timer_auto_stopped") + 1 :], "idle must follow timer_auto_stopped"


async def test_cancelled_timer_clears_the_countdown_and_returns_to_idle(session: PeripheralSession) -> None:
    """A cancelled timer clears the countdown on the peripheral and LVA returns to idle."""
    ha, peripheral = session.ha, session.peripheral
    events = peripheral.mark()
    ha.timer_event(VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_STARTED, "t2", 60, 60)
    await peripheral.wait_for("timer_ticking", events)
    updates = peripheral.mark()

    ha.timer_event(VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_CANCELLED, "t2", 60, 30)

    updated = await peripheral.wait_for("timer_updated", updates)
    assert updated["data"]["seconds_left"] == 0
    await peripheral.wait_for("idle", updates)


async def test_media_commands_control_the_music(session: PeripheralSession, speaker: SpeakerRecorder, media_server: FixtureServer) -> None:
    """The media commands of a peripheral control the music playback."""
    ha, peripheral = session.ha, session.peripheral
    events = peripheral.mark()

    ha.media_player_command(url=f"{media_server.base_url}/music_first.wav")

    await peripheral.wait_for("media_player_playing", events)
    assert await speaker.wait_for_tone(speaker.mark(), FIRST_MUSIC_TONE_HZ, timeout=8)

    await peripheral.command("pause_media_player")

    await ha.wait_for_media_state(MediaPlayerState.PAUSED)
    await asyncio.sleep(0.5)
    assert not await speaker.wait_for_tone(speaker.mark(), FIRST_MUSIC_TONE_HZ, timeout=1), "the music kept playing while paused"

    await peripheral.command("resume_media_player")

    await ha.wait_for_media_state(MediaPlayerState.PLAYING)
    assert await speaker.wait_for_tone(speaker.mark(), FIRST_MUSIC_TONE_HZ, timeout=5)

    await peripheral.command("stop_media_player")

    await ha.wait_for_media_state(MediaPlayerState.IDLE)


async def test_button_presses_reach_home_assistant(session: PeripheralSession, speaker: SpeakerRecorder) -> None:
    """Button presses of a peripheral arrive in Home Assistant as button events."""
    ha, peripheral = session.ha, session.peripheral
    button = ha.entity_by_name("Button Press")
    speaker_mark = speaker.mark()

    await peripheral.command("button_single_press")
    await wait_until(lambda: button.key in ha.states and ha.states[button.key].event_type == "single_press", description="the single press event")

    await peripheral.command("button_double_press")
    await wait_until(lambda: ha.states[button.key].event_type == "double_press", description="the double press event")
    assert await speaker.wait_for_sound(speaker_mark, timeout=5), "the double press sound was not played"

    await peripheral.command("button_triple_press")
    await wait_until(lambda: ha.states[button.key].event_type == "triple_press", description="the triple press event")

    await peripheral.command("button_long_press")
    await wait_until(lambda: ha.states[button.key].event_type == "long_press", description="the long press event")


async def test_button_lock_from_home_assistant_is_reported(session: PeripheralSession, peripheral_lva: LvaProcess) -> None:
    """The button lock switched in Home Assistant is reported to the peripheral."""
    ha, peripheral = session.ha, session.peripheral
    events = peripheral.mark()
    lock = ha.entity_by_name("Disable button controls")

    ha.client.switch_command(lock.key, True)

    await peripheral.wait_for("button_lock_changed", events, where=lambda data: data["locked"] is True)
    other = PeripheralClient()
    await other.connect(peripheral_lva.peripheral_port)
    assert other.snapshot["button_controls_locked"] is True
    await other.disconnect()

    ha.client.switch_command(lock.key, False)

    await peripheral.wait_for("button_lock_changed", events, where=lambda data: data["locked"] is False)


async def test_light_command_from_home_assistant_is_forwarded(session: PeripheralSession) -> None:
    """A light command from Home Assistant is forwarded to the peripheral."""
    ha, peripheral = session.ha, session.peripheral
    events = peripheral.mark()
    light = ha.entity_by_name("Web Console LEDs")

    ha.client.light_command(light.key, state=True, brightness=0.5, rgb=(1.0, 0.0, 0.0), effect="Voice Assistant")

    command = await peripheral.wait_for("light_command", events)
    data = command["data"]
    assert data["object_id"] == "web_console_leds"
    assert data["state"] is True
    assert data["effect"] == "Voice Assistant"
    assert abs(data["brightness"] - 0.5) < 0.01
    assert data["red"] > 0.9 and data["green"] < 0.1 and data["blue"] < 0.1


async def test_losing_home_assistant_is_reported(session: PeripheralSession, peripheral_lva: LvaProcess) -> None:
    """Losing the Home Assistant connection is reported to the peripheral."""
    ha, peripheral = session.ha, session.peripheral
    events = peripheral.mark()

    await ha.disconnect()

    await peripheral.wait_for("disconnected", events)
    other = PeripheralClient()
    await other.connect(peripheral_lva.peripheral_port)
    assert other.snapshot["ha_connected"] is False
    await other.disconnect()

    again = FakeHomeAssistant()
    await again.connect(peripheral_lva.api_port)
    await peripheral.wait_for("zeroconf", events, where=lambda data: data["status"] == "connected")
    await again.disconnect()


async def _register_like_web_console(lva: LvaProcess, ha: FakeHomeAssistant, peripheral: PeripheralClient) -> FakeHomeAssistant:
    """Send the registrations of the web console and return the Home Assistant connection LVA forces to reconnect."""
    await peripheral.command("register_light", LIGHT_REGISTRATION)
    await peripheral.command("register_button")
    await peripheral.command("register_button_lock")
    await wait_until(lambda: ha.connection_lost, 15, "the reconnect of Home Assistant after the registrations")
    _registered.add(lva.api_port)
    reconnected = FakeHomeAssistant()
    await reconnected.connect(lva.api_port)
    return reconnected
