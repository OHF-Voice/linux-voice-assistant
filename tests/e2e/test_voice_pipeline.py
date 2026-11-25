"""Wake word, voice request, spoken reply and stop word, from the virtual microphone to the virtual speaker."""

import asyncio
from pathlib import Path

import numpy as np

from .audio import PROBE_TONE_HZ, TTS_TONE_HZ, SpeakerRecorder, has_tone, play_into_mic
from .fake_ha import FakeHomeAssistant, FixtureServer
from .lva import LvaProcess

FIXTURES_DIR = Path(__file__).parent / "fixtures"


async def test_wake_word_runs_pipeline_and_plays_reply(lva: LvaProcess, ha: FakeHomeAssistant, speaker: SpeakerRecorder, media_server: FixtureServer) -> None:
    log = lva.log_mark()
    speaker_mark = speaker.mark()

    await play_into_mic(FIXTURES_DIR / "okay_nabu.wav")

    await lva.wait_for_log_line("Detected wake word: ", log)
    assert "Okay Nabu" in lva.log_since(log).split("Detected wake word: ", 1)[1].splitlines()[0]
    start = await ha.wait_for_start(1, timeout=10)
    assert "Okay Nabu" in (start.wake_word_phrase or "")
    assert await speaker.wait_for_sound(speaker_mark, timeout=5), "the wake sound was not played"

    ha.run_pipeline("turn on the light", "The light is on", f"{media_server.base_url}/tts_marker.wav")

    await lva.wait_for_log_line("Playing TTS response", log)
    assert await speaker.wait_for_tone(speaker_mark, TTS_TONE_HZ, timeout=10), "the reply audio was not played"
    await lva.wait_for_log_line("TTS response finished", log)


async def test_unrelated_speech_does_not_wake(lva: LvaProcess, ha: FakeHomeAssistant) -> None:
    log = lva.log_mark()

    await play_into_mic(FIXTURES_DIR / "unrelated_speech.wav")
    await asyncio.sleep(1.0)

    assert not ha.starts
    assert "Detected wake word" not in lva.log_since(log)


async def test_other_wake_word_does_not_wake(lva: LvaProcess, ha: FakeHomeAssistant) -> None:
    log = lva.log_mark()

    await play_into_mic(FIXTURES_DIR / "hey_jarvis.wav")
    await asyncio.sleep(1.0)

    assert not ha.starts
    assert "Detected wake word" not in lva.log_since(log)


async def test_microphone_audio_is_streamed_to_home_assistant(lva: LvaProcess, ha: FakeHomeAssistant) -> None:
    log = lva.log_mark()
    await play_into_mic(FIXTURES_DIR / "okay_nabu.wav")
    await ha.wait_for_start(1, timeout=10)
    audio_mark = len(ha.audio)

    await play_into_mic(FIXTURES_DIR / "probe_tone.wav")

    received = np.frombuffer(ha.audio_received_since(audio_mark), dtype=np.int16)
    assert has_tone(received, PROBE_TONE_HZ), "the tone played into the virtual microphone did not arrive at Home Assistant"

    ha.run_pipeline("never mind", None, None)
    await lva.wait_for_log_line("TTS response finished", log)


async def test_run_without_reply_returns_to_idle(lva: LvaProcess, ha: FakeHomeAssistant) -> None:
    log = lva.log_mark()
    await play_into_mic(FIXTURES_DIR / "okay_nabu.wav")
    await ha.wait_for_start(1, timeout=10)

    ha.run_pipeline("never mind", None, None)

    await lva.wait_for_log_line("Playing pipeline ended sound", log)
    assert "Playing TTS response" not in lva.log_since(log)


async def test_pipeline_error_is_reported_and_lva_recovers(lva: LvaProcess, ha: FakeHomeAssistant) -> None:
    log = lva.log_mark()
    await play_into_mic(FIXTURES_DIR / "okay_nabu.wav")
    await ha.wait_for_start(1, timeout=10)

    ha.send_error("stt-no-text-recognized", "No text recognized")
    ha.run_pipeline("", None, None)

    await lva.wait_for_log_line("TTS response finished", log)

    # LVA must accept the next wake word, a stuck pipeline would swallow it
    await asyncio.sleep(2.5)
    await play_into_mic(FIXTURES_DIR / "okay_nabu.wav")
    await ha.wait_for_start(2, timeout=10)
    ha.run_pipeline("never mind", None, None)


async def test_continue_conversation_listens_again_without_wake_word(lva: LvaProcess, ha: FakeHomeAssistant, media_server: FixtureServer) -> None:
    log = lva.log_mark()
    await play_into_mic(FIXTURES_DIR / "okay_nabu.wav")
    first = await ha.wait_for_start(1, timeout=10)

    ha.run_pipeline("start a timer", "For how long?", f"{media_server.base_url}/tts_marker.wav", continue_conversation=True)

    follow_up = await ha.wait_for_start(2, timeout=15)
    assert follow_up.wake_word_phrase == first.wake_word_phrase, "a follow-up runs on the pipeline of the wake word that started the conversation"
    ha.run_pipeline("never mind", None, None)
    await lva.wait_for_log_line("Playing pipeline ended sound", log)
