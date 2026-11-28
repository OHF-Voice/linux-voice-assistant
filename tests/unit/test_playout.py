"""Tests for the playout delay after the end of a file."""

import logging

import pytest

from linux_voice_assistant.player.playout import (
    PLAYOUT_MARGIN_SECONDS,
    playout_delay_seconds,
)


def _delay(duration, position, position_age=0.0, fallback_seconds=0.9, max_seconds=1.6):
    return playout_delay_seconds(duration, position, position_age=position_age, fallback_seconds=fallback_seconds, max_seconds=max_seconds)


def test_short_file_waits_only_for_its_own_length():
    assert _delay(0.3, 0.0) == pytest.approx(0.3 + PLAYOUT_MARGIN_SECONDS)


def test_long_file_waits_for_the_part_still_in_the_buffer():
    assert _delay(3.0, 2.05) == pytest.approx(0.95 + PLAYOUT_MARGIN_SECONDS)


def test_missing_position_counts_as_nothing_played():
    assert _delay(0.5, None, position_age=0.3) == pytest.approx(0.5 + PLAYOUT_MARGIN_SECONDS)


def test_time_since_the_position_report_counts_as_played():
    assert _delay(1.0, 0.0, position_age=0.17) == pytest.approx(0.83 + PLAYOUT_MARGIN_SECONDS)


def test_position_below_zero_is_audio_still_on_its_way():
    assert _delay(1.0, -0.03, position_age=0.1) == pytest.approx(0.93 + PLAYOUT_MARGIN_SECONDS)


def test_played_time_is_never_below_zero():
    assert _delay(1.0, -0.2, position_age=0.0) == pytest.approx(1.0 + PLAYOUT_MARGIN_SECONDS)


def test_position_past_the_end_waits_only_the_margin():
    assert _delay(1.0, 1.2) == pytest.approx(PLAYOUT_MARGIN_SECONDS)


def test_audio_left_is_limited():
    assert _delay(60.0, 1.0, max_seconds=1.6) == pytest.approx(1.6 + PLAYOUT_MARGIN_SECONDS)


@pytest.mark.parametrize("duration", [None, 0.0, -1.0])
def test_unknown_duration_uses_the_fallback(duration):
    assert _delay(duration, 0.2, fallback_seconds=0.9) == 0.9


def test_delay_is_logged(caplog):
    with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.player.playout"):
        _delay(0.3, 0.0)

    assert "Playout delay 0.40 s: duration 0.30 s, played 0.00 s, left 0.30 s (limit 1.60 s)" in caplog.text


def test_fallback_is_logged(caplog):
    with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.player.playout"):
        _delay(None, 0.2)

    assert "Playout delay 0.90 s: duration unknown (None), using the fallback" in caplog.text
