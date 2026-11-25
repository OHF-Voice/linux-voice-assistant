"""Checks of the test setup itself, before LVA is involved."""

from pathlib import Path

from .audio import PROBE_TONE_HZ, TTS_TONE_HZ, SpeakerRecorder, play_into_mic

FIXTURES_DIR = Path(__file__).parent / "fixtures"


async def test_speaker_recorder_hears_marker_tone(speaker: SpeakerRecorder) -> None:
    import asyncio

    mark = speaker.mark()
    process = await asyncio.create_subprocess_exec("paplay", "--device=e2e_speaker", str(FIXTURES_DIR / "tts_marker.wav"))
    await process.wait()

    assert await speaker.wait_for_tone(mark, TTS_TONE_HZ, timeout=3)
    assert not await speaker.wait_for_tone(mark, PROBE_TONE_HZ, timeout=0.2)


async def test_play_into_mic_does_not_reach_speaker(speaker: SpeakerRecorder) -> None:
    mark = speaker.mark()
    await play_into_mic(FIXTURES_DIR / "probe_tone.wav")

    assert not await speaker.wait_for_sound(mark, timeout=0.5)
