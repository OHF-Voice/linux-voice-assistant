"""The stop word ends a spoken reply or a ringing timer, and does nothing while LVA is idle."""

import asyncio
from pathlib import Path

from aioesphomeapi import VoiceAssistantTimerEventType

from .audio import TTS_TONE_HZ, SpeakerRecorder, play_into_mic
from .fake_ha import FakeHomeAssistant, FixtureServer
from .lva import LvaProcess

FIXTURES_DIR = Path(__file__).parent / "fixtures"


async def test_stop_word_ends_spoken_reply(lva: LvaProcess, ha: FakeHomeAssistant, speaker: SpeakerRecorder, media_server: FixtureServer) -> None:
    log = lva.log_mark()
    await play_into_mic(FIXTURES_DIR / "okay_nabu.wav")
    await ha.wait_for_start(1, timeout=10)
    ha.run_pipeline("tell me a story", "Once upon a time", f"{media_server.base_url}/tts_marker_long.wav")
    await lva.wait_for_log_line("Playing TTS response", log)
    assert await speaker.wait_for_tone(speaker.mark(), TTS_TONE_HZ, timeout=5)

    await play_into_mic(FIXTURES_DIR / "stop.wav")

    await lva.wait_for_log_line("Stop word detected", log, timeout=8)
    await lva.wait_for_log_line("TTS response finished", log, timeout=8)
    await asyncio.sleep(0.5)
    assert not await speaker.wait_for_tone(speaker.mark(), TTS_TONE_HZ, timeout=1.5), "the reply kept playing after the stop word"


async def test_stop_word_is_ignored_while_idle(lva: LvaProcess, ha: FakeHomeAssistant) -> None:
    log = lva.log_mark()

    await play_into_mic(FIXTURES_DIR / "stop.wav")
    await asyncio.sleep(1.0)

    assert "Stop word detected" not in lva.log_since(log)
    assert not ha.starts


async def test_stop_word_stops_ringing_timer(lva: LvaProcess, ha: FakeHomeAssistant, speaker: SpeakerRecorder) -> None:
    log = lva.log_mark()
    speaker_mark = speaker.mark()
    ha.timer_event(VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_STARTED, "t1", 60, 60)

    ha.timer_event(VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_FINISHED, "t1", 60, 0)
    assert await speaker.wait_for_sound(speaker_mark, timeout=5), "the timer sound was not played"

    await play_into_mic(FIXTURES_DIR / "stop.wav")

    await lva.wait_for_log_line("Stopping timer finished sound", log, timeout=8)
    await asyncio.sleep(2.5)
    assert not await speaker.wait_for_sound(speaker.mark(), timeout=2), "the timer kept ringing after the stop word"
