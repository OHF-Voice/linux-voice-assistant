"""Unit tests for the buffer and the messages of the wake word audio in wake_word_audio.py."""

import logging

import numpy as np
import pytest
from aioesphomeapi.model import VoiceAssistantSubscriptionFlag

from linux_voice_assistant.models import DetectionResult
from linux_voice_assistant.wake_word_audio import (
    AudioChunk,
    WakeWordAudio,
    audio_level,
    audio_messages,
    buffer_wake_word_audio,
    is_wake_word_audio_requested,
    start_request,
    take_wake_word_audio,
)

BUFFER_BYTES = 48000  # 1.5 s at 16 kHz, 16 bit


def chunk(size: int = 2048, value: int = 0) -> AudioChunk:
    pcm = np.full(size // 2, value, dtype="<i2").tobytes()
    return AudioChunk(pcm, pcm)


def fill(count: int, size: int = 2048) -> tuple:
    buffer: tuple = ()
    for index in range(count):
        buffer = buffer_wake_word_audio(buffer, chunk(size, index), buffering=True)
    return buffer


class TestIsWakeWordAudioRequested:
    def test_flag_set(self):
        assert is_wake_word_audio_requested(VoiceAssistantSubscriptionFlag.API_AUDIO | VoiceAssistantSubscriptionFlag.WAKE_WORD_AUDIO)

    def test_flag_missing(self):
        assert not is_wake_word_audio_requested(VoiceAssistantSubscriptionFlag.API_AUDIO)

    def test_not_subscribed(self):
        assert not is_wake_word_audio_requested(None)


class TestBufferWakeWordAudio:
    def test_adds_blocks(self):
        assert len(fill(3)) == 3

    def test_keeps_at_least_the_buffer_length_plus_one_block(self):
        buffer = fill(100)

        total = sum(len(block.data) for block in buffer)
        assert BUFFER_BYTES <= total < BUFFER_BYTES + 2048

    def test_drops_the_oldest_blocks(self):
        buffer = fill(100)

        assert buffer[-1] == chunk(2048, 99)
        assert chunk(2048, 0) not in buffer

    def test_a_short_channel_keeps_older_blocks(self):
        # data is shorter while WebRTC holds back part of a frame, so blocks are only dropped once both channels are long enough
        buffer: tuple = ()
        for _ in range(30):
            buffer = buffer_wake_word_audio(buffer, AudioChunk(b"\x00" * 1920, b"\x00" * 2048), buffering=True)

        assert sum(len(block.data) for block in buffer) >= BUFFER_BYTES
        assert sum(len(block.data2) for block in buffer) >= BUFFER_BYTES

    def test_pause_empties_the_buffer(self, caplog):
        buffer = fill(3)

        with caplog.at_level(logging.DEBUG):
            buffer = buffer_wake_word_audio(buffer, chunk(), buffering=False)

        assert buffer == ()
        assert "Wake word audio buffer paused, 6144 bytes dropped" in caplog.text

    def test_pause_is_logged_once(self, caplog):
        with caplog.at_level(logging.DEBUG):
            buffer_wake_word_audio((), chunk(), buffering=False)

        assert "paused" not in caplog.text

    def test_start_is_logged(self, caplog):
        with caplog.at_level(logging.DEBUG):
            buffer = buffer_wake_word_audio((), chunk(), buffering=True)
            buffer_wake_word_audio(buffer, chunk(), buffering=True)

        assert caplog.text.count("Wake word audio buffer started (1.5 s)") == 1


class TestTakeWakeWordAudio:
    def test_last_buffer_length_of_each_channel(self):
        audio = take_wake_word_audio(fill(100), "hey_jarvis")

        assert audio is not None
        assert len(audio.data) == BUFFER_BYTES
        assert len(audio.data2) == BUFFER_BYTES
        assert audio.data[-2048:] == chunk(2048, 99).data

    def test_channels_are_cut_on_their_own(self):
        buffer = (AudioChunk(b"\x01\x00" * 100, b"\x02\x00" * 110),)

        audio = take_wake_word_audio(buffer, "hey_jarvis")

        assert audio == WakeWordAudio(b"\x01\x00" * 100, b"\x02\x00" * 110)

    def test_short_buffer_is_sent_whole(self):
        audio = take_wake_word_audio(fill(2), "hey_jarvis")

        assert audio is not None
        assert len(audio.data) == 4096

    def test_empty_channel_gives_nothing(self, caplog):
        with caplog.at_level(logging.DEBUG):
            assert take_wake_word_audio((AudioChunk(b"", b"\x00\x00"),), "hey_jarvis") is None

        assert "No wake word audio for 'hey_jarvis'" in caplog.text

    def test_level_is_logged(self, caplog):
        with caplog.at_level(logging.DEBUG):
            take_wake_word_audio((chunk(2048, 16384),), "hey_jarvis")

        assert "Wake word audio for 'hey_jarvis': data 2048 bytes (peak -6.0 dBFS, RMS -6.0 dBFS), data2 2048 bytes" in caplog.text


class TestStartRequest:
    def test_fields(self):
        detection = DetectionResult("Internal - MWW - Hey Jarvis", 0, 0.93, 0.7)

        request = start_request("Hey Jarvis", detection, WakeWordAudio(b"\x00" * 100, b"\x00" * 120))

        assert request.start
        assert request.wake_word_phrase == "Hey Jarvis"
        assert request.wake_word_id == "Internal - MWW - Hey Jarvis"
        assert request.wake_word_audio_bytes == 100
        assert request.wake_word_audio_bytes2 == 120
        assert request.wake_word_probability == pytest.approx(0.93)
        assert request.wake_word_probability_cutoff == pytest.approx(0.7)

    def test_missing_score_is_zero(self):
        request = start_request("Hey Jarvis", DetectionResult("id", 0), WakeWordAudio(b"\x00\x00", b"\x00\x00"))

        assert request.wake_word_probability == 0.0
        assert request.wake_word_probability_cutoff == 0.0


class TestAudioMessages:
    def test_all_audio_is_sent_in_order(self):
        audio = take_wake_word_audio(fill(100), "id")
        assert audio is not None

        messages = audio_messages(audio)

        assert b"".join(m.data for m in messages) == audio.data
        assert b"".join(m.data2 for m in messages) == audio.data2
        assert all(len(m.data) <= 2048 for m in messages)

    def test_channels_of_different_length_have_no_empty_part(self):
        audio = WakeWordAudio(b"\x01\x00" * 1000, b"\x02\x00" * 1100)

        messages = audio_messages(audio)

        assert all(m.data and m.data2 for m in messages)
        assert b"".join(m.data for m in messages) == audio.data
        assert b"".join(m.data2 for m in messages) == audio.data2

    def test_parts_hold_whole_samples(self):
        messages = audio_messages(WakeWordAudio(b"\x01\x00" * 3001, b"\x02\x00" * 3000))

        assert all(len(m.data) % 2 == 0 and len(m.data2) % 2 == 0 for m in messages)


class TestAudioLevel:
    def test_full_scale(self):
        peak, rms = audio_level(np.full(100, -32768, dtype="<i2").tobytes())
        assert peak == pytest.approx(0.0)
        assert rms == pytest.approx(0.0)

    def test_silence(self):
        assert audio_level(b"\x00\x00" * 10) == (-120.0, -120.0)

    def test_empty(self):
        assert audio_level(b"") == (-120.0, -120.0)
