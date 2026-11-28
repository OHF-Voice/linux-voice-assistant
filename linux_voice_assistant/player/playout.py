"""How long the audio output still plays after mpv reported the end of a file.

mpv reports the end of a file as soon as its last samples are handed to the audio output,
not when they have been heard. A file shorter than the output buffer is handed over at once,
so the end is reported right at its start. The time still to wait is the duration of the
file minus the position mpv has played so far. mpv reports that position with the output
latency already taken into account (below 0 while the first samples are still on their way),
but only about ten times a second, so the time since the last report is added.
"""

import logging
from typing import Optional

_LOGGER = logging.getLogger(__name__)

PLAYOUT_MARGIN_SECONDS = 0.1


def playout_delay_seconds(duration: Optional[float], position: Optional[float], position_age: float, fallback_seconds: float, max_seconds: float) -> float:
    """
    Return how long to wait after the end of a file until its audio has been played out.
    :param duration: Duration of the file in seconds, None if mpv did not report one (for example a stream).
    :param position: Last playback position mpv reported for the file in seconds, None if it reported none.
    :param position_age: Seconds since mpv reported that position. The file played on during that time.
    :param fallback_seconds: Delay used when the duration is unknown.
    :param max_seconds: Upper limit for the audio still left, so a stale position cannot hold the callback back for long.

    :py:data:`PLAYOUT_MARGIN_SECONDS` is added to the audio still left. Without a position
    nothing counts as played yet.
    """
    if duration is None or duration <= 0:
        _LOGGER.debug("Playout delay %.2f s: duration unknown (%s), using the fallback", fallback_seconds, duration)
        return fallback_seconds

    played = max(0.0, position + position_age) if position is not None else 0.0
    remaining = min(max(0.0, duration - played), max_seconds)
    delay = remaining + PLAYOUT_MARGIN_SECONDS
    _LOGGER.debug("Playout delay %.2f s: duration %.2f s, played %.2f s, left %.2f s (limit %.2f s)", delay, duration, played, remaining, max_seconds)
    return delay
