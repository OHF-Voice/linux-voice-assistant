"""Wake word, voice request, spoken reply and stop word, from the virtual microphone to the virtual speaker."""

from pathlib import Path

from .audio import TTS_TONE_HZ, SpeakerRecorder, play_into_mic
from .fake_ha import FakeHomeAssistant, FixtureServer
from .lva import LvaProcess
from .peripheral import PeripheralClient

FIXTURES_DIR = Path(__file__).parent / "fixtures"


async def test_wake_word_runs_pipeline_and_plays_reply(
    lva: LvaProcess, ha: FakeHomeAssistant, peripheral: PeripheralClient, speaker: SpeakerRecorder, media_server: FixtureServer
) -> None:
    events = peripheral.mark()
    speaker_mark = speaker.mark()

    await play_into_mic(FIXTURES_DIR / "okay_nabu.wav")

    detected = await peripheral.wait_for("wake_word_detected", events)
    assert "Okay Nabu" in detected["data"]["wake_word_id"]
    start = await ha.wait_for_start(1, timeout=10)
    assert "Okay Nabu" in (start.wake_word_phrase or "")
    assert await speaker.wait_for_sound(speaker_mark, timeout=5), "the wake sound was not played"
    await peripheral.wait_for("listening", events)

    ha.run_pipeline("turn on the light", "The light is on", f"{media_server.base_url}/tts_marker.wav")

    await peripheral.wait_for("thinking", events)
    await peripheral.wait_for("tts_speaking", events)
    assert await speaker.wait_for_tone(speaker_mark, TTS_TONE_HZ, timeout=10), "the reply audio was not played"
    await peripheral.wait_for("tts_finished", events)
    await peripheral.wait_for("idle", events)
