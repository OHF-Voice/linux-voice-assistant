"""Wake word, voice request, spoken reply and stop word, from the virtual microphone to the virtual speaker."""

import asyncio
from pathlib import Path

import numpy as np

from .audio import PROBE_TONE_HZ, TTS_TONE_HZ, SpeakerRecorder, has_tone, play_into_mic
from .fake_ha import FakeHomeAssistant, FixtureServer
from .peripheral import PeripheralClient

FIXTURES_DIR = Path(__file__).parent / "fixtures"


async def test_wake_word_runs_pipeline_and_plays_reply(ha: FakeHomeAssistant, peripheral: PeripheralClient, speaker: SpeakerRecorder, media_server: FixtureServer) -> None:
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


async def test_unrelated_speech_does_not_wake(ha: FakeHomeAssistant, peripheral: PeripheralClient) -> None:
    events = peripheral.mark()

    await play_into_mic(FIXTURES_DIR / "unrelated_speech.wav")
    await asyncio.sleep(1.0)

    assert not ha.starts
    assert "wake_word_detected" not in peripheral.names_since(events)


async def test_other_wake_word_does_not_wake(ha: FakeHomeAssistant, peripheral: PeripheralClient) -> None:
    events = peripheral.mark()

    await play_into_mic(FIXTURES_DIR / "hey_jarvis.wav")
    await asyncio.sleep(1.0)

    assert not ha.starts
    assert "wake_word_detected" not in peripheral.names_since(events)


async def test_microphone_audio_is_streamed_to_home_assistant(ha: FakeHomeAssistant, peripheral: PeripheralClient) -> None:
    events = peripheral.mark()
    await play_into_mic(FIXTURES_DIR / "okay_nabu.wav")
    await ha.wait_for_start(1, timeout=10)
    await peripheral.wait_for("listening", events)
    audio_mark = len(ha.audio)

    await play_into_mic(FIXTURES_DIR / "probe_tone.wav")

    received = np.frombuffer(ha.audio_received_since(audio_mark), dtype=np.int16)
    assert has_tone(received, PROBE_TONE_HZ), "the tone played into the virtual microphone did not arrive at Home Assistant"

    ha.run_pipeline("never mind", None, None)
    await peripheral.wait_for("idle", events)


async def test_run_without_reply_returns_to_idle(ha: FakeHomeAssistant, peripheral: PeripheralClient) -> None:
    events = peripheral.mark()
    await play_into_mic(FIXTURES_DIR / "okay_nabu.wav")
    await ha.wait_for_start(1, timeout=10)
    await peripheral.wait_for("listening", events)

    ha.run_pipeline("never mind", None, None)

    await peripheral.wait_for("idle", events)
    assert "tts_speaking" not in peripheral.names_since(events)


async def test_pipeline_error_is_reported_and_lva_recovers(ha: FakeHomeAssistant, peripheral: PeripheralClient) -> None:
    events = peripheral.mark()
    await play_into_mic(FIXTURES_DIR / "okay_nabu.wav")
    await ha.wait_for_start(1, timeout=10)
    await peripheral.wait_for("listening", events)

    ha.send_error("stt-no-text-recognized", "No text recognized")
    ha.run_pipeline("", None, None)

    await peripheral.wait_for("pipeline_error", events)
    await peripheral.wait_for("idle", events)

    # LVA must accept the next wake word, a stuck pipeline would swallow it
    await asyncio.sleep(2.5)
    await play_into_mic(FIXTURES_DIR / "okay_nabu.wav")
    await ha.wait_for_start(2, timeout=10)
    ha.run_pipeline("never mind", None, None)
    await peripheral.wait_for("idle", events)


async def test_continue_conversation_listens_again_without_wake_word(ha: FakeHomeAssistant, peripheral: PeripheralClient, media_server: FixtureServer) -> None:
    events = peripheral.mark()
    await play_into_mic(FIXTURES_DIR / "okay_nabu.wav")
    first = await ha.wait_for_start(1, timeout=10)
    await peripheral.wait_for("listening", events)

    ha.run_pipeline("start a timer", "For how long?", f"{media_server.base_url}/tts_marker.wav", continue_conversation=True)

    follow_up = await ha.wait_for_start(2, timeout=15)
    assert follow_up.wake_word_phrase == first.wake_word_phrase, "a follow-up runs on the pipeline of the wake word that started the conversation"
    ha.run_pipeline("never mind", None, None)
    await peripheral.wait_for("idle", events)
