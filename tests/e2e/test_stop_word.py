"""The stop word ends a spoken reply or a ringing timer, and does nothing while LVA is idle."""

import asyncio
from pathlib import Path

from aioesphomeapi import VoiceAssistantTimerEventType

from .audio import TTS_TONE_HZ, SpeakerRecorder, play_into_mic
from .fake_ha import FakeHomeAssistant, FixtureServer
from .lva import LvaProcess
from .peripheral import PeripheralClient

FIXTURES_DIR = Path(__file__).parent / "fixtures"


async def test_stop_word_ends_spoken_reply(lva: LvaProcess, ha: FakeHomeAssistant, peripheral: PeripheralClient, speaker: SpeakerRecorder, media_server: FixtureServer) -> None:
    events = peripheral.mark()
    await play_into_mic(FIXTURES_DIR / "okay_nabu.wav")
    await ha.wait_for_start(1, timeout=10)
    await peripheral.wait_for("listening", events)
    ha.run_pipeline("tell me a story", "Once upon a time", f"{media_server.base_url}/tts_marker_long.wav")
    await peripheral.wait_for("tts_speaking", events)
    assert await speaker.wait_for_tone(speaker.mark(), TTS_TONE_HZ, timeout=5)

    await play_into_mic(FIXTURES_DIR / "stop.wav")

    await peripheral.wait_for("tts_finished", events, timeout=8)
    await peripheral.wait_for("idle", events)
    await asyncio.sleep(0.5)
    assert not await speaker.wait_for_tone(speaker.mark(), TTS_TONE_HZ, timeout=1.5), "the reply kept playing after the stop word"
    assert lva.wait_for_log("Stop word detected", 1)


async def test_stop_word_is_ignored_while_idle(lva: LvaProcess, peripheral: PeripheralClient) -> None:
    events = peripheral.mark()
    log = lva.log_mark()

    await play_into_mic(FIXTURES_DIR / "stop.wav")
    await asyncio.sleep(1.0)

    assert "Stop word detected" not in lva.log_since(log)
    assert not peripheral.names_since(events)


async def test_stop_word_stops_ringing_timer(ha: FakeHomeAssistant, peripheral: PeripheralClient, speaker: SpeakerRecorder) -> None:
    events = peripheral.mark()
    speaker_mark = speaker.mark()
    ha.timer_event(VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_STARTED, "t1", 60, 60)
    await peripheral.wait_for("timer_ticking", events)

    ha.timer_event(VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_FINISHED, "t1", 60, 0)
    await peripheral.wait_for("timer_ringing", events)
    assert await speaker.wait_for_sound(speaker_mark, timeout=5), "the timer sound was not played"

    await play_into_mic(FIXTURES_DIR / "stop.wav")

    await peripheral.wait_for("idle", events, timeout=8)
    await asyncio.sleep(2.5)
    assert not await speaker.wait_for_sound(speaker.mark(), timeout=2), "the timer kept ringing after the stop word"
