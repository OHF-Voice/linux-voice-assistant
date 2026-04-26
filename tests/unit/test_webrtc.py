"""Unit tests for WebRTCProcessor."""

import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

FRAME_SIZE = 320  # 160 samples * 2 bytes (16-bit PCM)
PATCH_TARGET = "webrtc_noise_gain.AudioProcessor"


def make_audio(n_bytes: int, fill: int = 0xAB) -> bytes:
    """Return n_bytes of dummy PCM data."""
    return bytes([fill]) * n_bytes


def make_mock_apm(output_fill: int = 0x00):
    """Return a mock AudioProcessor whose Process10ms returns a frame-sized result."""
    mock_apm = MagicMock()
    mock_result = MagicMock()
    mock_result.audio = bytes([output_fill]) * FRAME_SIZE
    mock_apm.Process10ms.return_value = mock_result
    return mock_apm


@pytest.fixture
def processor():
    """WebRTCProcessor with a mocked AudioProcessor so no C extension is needed."""
    mock_apm = make_mock_apm()
    with patch(PATCH_TARGET, return_value=mock_apm):
        from linux_voice_assistant.webrtc import WebRTCProcessor

        proc = WebRTCProcessor(agc_level=3, ns_level=2)
        proc._mock_apm = mock_apm
        yield proc


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestInit:
    def test_frame_size_is_320(self, processor):
        assert processor.FRAME_SIZE_BYTES == FRAME_SIZE

    def test_agc_stored(self, processor):
        assert processor.agc_level == 3

    def test_ns_stored(self, processor):
        assert processor.ns_level == 2

    def test_buffer_starts_empty(self, processor):
        assert len(processor._buffer) == 0

    def test_audio_processor_constructed_with_correct_levels(self):
        mock_apm = make_mock_apm()
        with patch(PATCH_TARGET, return_value=mock_apm) as mock_cls:
            from linux_voice_assistant.webrtc import WebRTCProcessor

            WebRTCProcessor(agc_level=5, ns_level=1)
            mock_cls.assert_called_once_with(5, 1)


# ---------------------------------------------------------------------------
# process() — buffering behaviour
# ---------------------------------------------------------------------------


class TestProcessBuffering:
    def test_exact_one_frame_returns_processed_bytes(self, processor):
        result = processor.process(make_audio(FRAME_SIZE))
        assert len(result) == FRAME_SIZE
        processor._mock_apm.Process10ms.assert_called_once()

    def test_less_than_one_frame_returns_empty(self, processor):
        result = processor.process(make_audio(FRAME_SIZE - 1))
        assert result == b""
        processor._mock_apm.Process10ms.assert_not_called()

    def test_two_frames_returns_two_processed_chunks(self, processor):
        result = processor.process(make_audio(FRAME_SIZE * 2))
        assert len(result) == FRAME_SIZE * 2
        assert processor._mock_apm.Process10ms.call_count == 2

    def test_partial_input_buffers_remainder(self, processor):
        # 500 bytes = 1 full frame (320) + 180 leftover
        result = processor.process(make_audio(500))
        assert len(result) == FRAME_SIZE
        assert len(processor._buffer) == 500 - FRAME_SIZE

    def test_accumulated_calls_eventually_flush(self, processor):
        # Two calls of 160 bytes each should flush one frame total
        processor.process(make_audio(160))
        assert processor._mock_apm.Process10ms.call_count == 0
        processor.process(make_audio(160))
        assert processor._mock_apm.Process10ms.call_count == 1

    def test_buffer_drains_in_place(self, processor):
        """After processing, only the remainder stays in the buffer."""
        processor.process(make_audio(500))
        assert len(processor._buffer) == 180

    def test_empty_input_returns_empty(self, processor):
        result = processor.process(b"")
        assert result == b""
        processor._mock_apm.Process10ms.assert_not_called()

    def test_output_is_concatenation_of_processed_chunks(self, processor):
        """Each frame gets processed independently and results are joined."""
        apm = processor._mock_apm
        apm.Process10ms.side_effect = [
            MagicMock(audio=b"\x01" * FRAME_SIZE),
            MagicMock(audio=b"\x02" * FRAME_SIZE),
        ]
        result = processor.process(make_audio(FRAME_SIZE * 2))
        assert result == b"\x01" * FRAME_SIZE + b"\x02" * FRAME_SIZE

    def test_multiple_process_calls_accumulate_buffer(self, processor):
        """Remainder from first call is used in second call."""
        processor.process(make_audio(200))   # 200 buffered, no flush
        processor.process(make_audio(200))   # 400 total, 1 flush, 80 remain
        assert processor._mock_apm.Process10ms.call_count == 1
        assert len(processor._buffer) == 80


# ---------------------------------------------------------------------------
# update_settings()
# ---------------------------------------------------------------------------


class TestUpdateSettings:
    def test_reinitializes_when_agc_changes(self, processor):
        new_apm = make_mock_apm()
        with patch(PATCH_TARGET, return_value=new_apm):
            processor.update_settings(agc_level=10, ns_level=2)
        assert processor.apm is new_apm
        assert processor.agc_level == 10

    def test_reinitializes_when_ns_changes(self, processor):
        new_apm = make_mock_apm()
        with patch(PATCH_TARGET, return_value=new_apm):
            processor.update_settings(agc_level=3, ns_level=4)
        assert processor.apm is new_apm
        assert processor.ns_level == 4

    def test_no_reinitialize_when_settings_unchanged(self, processor):
        original_apm = processor.apm
        with patch(PATCH_TARGET) as mock_cls:
            processor.update_settings(agc_level=3, ns_level=2)
            mock_cls.assert_not_called()
        assert processor.apm is original_apm

    def test_stores_new_agc_level(self, processor):
        with patch(PATCH_TARGET, return_value=make_mock_apm()):
            processor.update_settings(agc_level=15, ns_level=2)
        assert processor.agc_level == 15

    def test_stores_new_ns_level(self, processor):
        with patch(PATCH_TARGET, return_value=make_mock_apm()):
            processor.update_settings(agc_level=3, ns_level=3)
        assert processor.ns_level == 3

    def test_new_apm_called_with_updated_levels(self, processor):
        with patch(PATCH_TARGET, return_value=make_mock_apm()) as mock_cls:
            processor.update_settings(agc_level=7, ns_level=1)
            mock_cls.assert_called_once_with(7, 1)


# ---------------------------------------------------------------------------
# process() after update_settings()
# ---------------------------------------------------------------------------


class TestProcessAfterUpdate:
    def test_buffer_preserved_across_settings_update(self, processor):
        """Buffered bytes should survive a settings change."""
        processor.process(make_audio(160))  # half frame, stays buffered
        assert len(processor._buffer) == 160

        with patch(PATCH_TARGET, return_value=make_mock_apm()):
            processor.update_settings(agc_level=10, ns_level=2)

        assert len(processor._buffer) == 160

    def test_process_uses_new_apm_after_update(self, processor):
        new_apm = make_mock_apm(output_fill=0xFF)
        with patch(PATCH_TARGET, return_value=new_apm):
            processor.update_settings(agc_level=10, ns_level=2)

        processor.process(make_audio(FRAME_SIZE))
        new_apm.Process10ms.assert_called_once()