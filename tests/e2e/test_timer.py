"""A ringing timer: its sound, its effect on music and the ways to stop it."""

import asyncio
from pathlib import Path

from aioesphomeapi import MediaPlayerCommand, MediaPlayerState, VoiceAssistantTimerEventType

from .audio import FIRST_MUSIC_TONE_HZ, SpeakerRecorder, play_into_mic
from .fake_ha import FakeHomeAssistant, FixtureServer
from .lva import LvaProcess

FIXTURES_DIR = Path(__file__).parent / "fixtures"


async def test_ringing_timer_ducks_music_and_stop_word_restores_it(lva: LvaProcess, ha: FakeHomeAssistant, speaker: SpeakerRecorder, media_server: FixtureServer) -> None:
    log = lva.log_mark()
    ha.media_player_command(url=f"{media_server.base_url}/music_first.wav")
    assert await speaker.wait_for_tone(speaker.mark(), FIRST_MUSIC_TONE_HZ, timeout=8)
    await asyncio.sleep(1.0)
    normal = speaker.recent_amplitude(FIRST_MUSIC_TONE_HZ)

    ha.timer_event(VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_FINISHED, "t1", 60, 0)
    await lva.wait_for_log_line("Timer event: type=VOICE_ASSISTANT_TIMER_FINISHED", log)
    await asyncio.sleep(1.5)
    ducked = speaker.recent_amplitude(FIRST_MUSIC_TONE_HZ)

    await play_into_mic(FIXTURES_DIR / "stop.wav")
    await lva.wait_for_log_line("Stopping timer finished sound", log, timeout=8)
    await asyncio.sleep(1.5)
    restored = speaker.recent_amplitude(FIRST_MUSIC_TONE_HZ)
    ha.media_player_command(command=MediaPlayerCommand.STOP)
    await ha.wait_for_media_state(MediaPlayerState.IDLE)

    assert normal > 1000
    assert ducked < normal * 0.75, f"music not ducked by the timer: {normal:.0f} -> {ducked:.0f}"
    assert restored > normal * 0.85, f"music not restored after the timer: {normal:.0f} -> {restored:.0f}"


async def test_wake_word_silences_ringing_timer_and_starts_pipeline(lva: LvaProcess, ha: FakeHomeAssistant, speaker: SpeakerRecorder) -> None:
    log = lva.log_mark()
    ha.timer_event(VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_FINISHED, "t1", 60, 0)
    assert await speaker.wait_for_sound(speaker.mark(), timeout=5)

    await play_into_mic(FIXTURES_DIR / "okay_nabu.wav")

    await lva.wait_for_log_line("Detected wake word", log)
    await ha.wait_for_start(1, timeout=10)
    ha.run_pipeline("never mind", None, None)
    await lva.wait_for_log_line("TTS response finished", log)
    await asyncio.sleep(2.5)
    assert not await speaker.wait_for_sound(speaker.mark(), timeout=2), "the timer kept ringing after the wake word"
