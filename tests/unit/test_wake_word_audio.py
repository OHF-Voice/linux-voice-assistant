"""Unit tests for wake_word_audio.py — the buffer of audio recorded before a wake word detection."""

from linux_voice_assistant.wake_word_audio import WakeWordAudio, WakeWordAudioBuffer


class TestWakeWordAudio:
    def test_byte_counts_are_summed_per_channel(self):
        audio = WakeWordAudio(((b"aa", b"b"), (b"cccc", b"dd")))
        assert audio.audio_bytes == 6
        assert audio.audio_bytes2 == 3

    def test_empty_audio_has_no_bytes(self):
        audio = WakeWordAudio(())
        assert audio.audio_bytes == 0
        assert audio.audio_bytes2 == 0


class TestWakeWordAudioBuffer:
    def test_snapshot_returns_blocks_in_recording_order(self):
        buffer = WakeWordAudioBuffer(duration_seconds=1.0)
        buffer.append(b"\x01\x01", b"\x02\x02")
        buffer.append(b"\x03\x03", b"\x04\x04")
        assert buffer.snapshot().chunks == ((b"\x01\x01", b"\x02\x02"), (b"\x03\x03", b"\x04\x04"))

    def test_oldest_blocks_are_dropped_beyond_the_duration(self):
        # 0.001 s at 16 kHz / 16 bit = 32 bytes
        buffer = WakeWordAudioBuffer(duration_seconds=0.001)
        for value in range(5):
            buffer.append(bytes([value]) * 16, bytes([value]) * 16)

        audio = buffer.snapshot()
        assert audio.audio_bytes == 32
        assert [chunk[0] for chunk, _ in audio.chunks] == [bytes([3])[0], bytes([4])[0]]

    def test_a_single_block_larger_than_the_duration_is_kept(self):
        buffer = WakeWordAudioBuffer(duration_seconds=0.001)
        buffer.append(b"\x00" * 100, b"\x00" * 100)
        assert buffer.snapshot().audio_bytes == 100

    def test_snapshot_does_not_empty_the_buffer(self):
        buffer = WakeWordAudioBuffer(duration_seconds=1.0)
        buffer.append(b"\x00\x00", b"\x00\x00")
        buffer.snapshot()
        assert buffer.snapshot().audio_bytes == 2

    def test_clear_discards_everything(self):
        buffer = WakeWordAudioBuffer(duration_seconds=1.0)
        buffer.append(b"\x00\x00", b"\x00\x00")
        buffer.clear()
        assert buffer.snapshot().chunks == ()

    def test_buffer_is_usable_after_clear(self):
        buffer = WakeWordAudioBuffer(duration_seconds=0.001)
        buffer.append(b"\x00" * 100, b"\x00" * 100)
        buffer.clear()
        buffer.append(b"\x01" * 10, b"\x01" * 10)
        assert buffer.snapshot().audio_bytes == 10

    def test_second_channel_is_kept_with_its_block(self):
        buffer = WakeWordAudioBuffer(duration_seconds=1.0)
        buffer.append(b"\x01\x01", b"\x02\x02\x02\x02")
        audio = buffer.snapshot()
        assert audio.audio_bytes == 2
        assert audio.audio_bytes2 == 4
