"""Generate the audio fixtures of the end-to-end tests.

Speech is synthesized with Piper, the tone markers are computed. The generated files are committed, so CI does not need Piper.
Run this only when a fixture has to change:

    python -m venv /tmp/tts && /tmp/tts/bin/pip install piper-tts numpy
    /tmp/tts/bin/python -m piper.download_voices en_US-lessac-medium --data-dir /tmp/voices
    /tmp/tts/bin/python tests/e2e/generate_fixtures.py /tmp/voices/en_US-lessac-medium.onnx

Without the voice argument only the tone files are written. Piper output is not reproducible bit by bit,
so regenerating the speech changes the committed files even when nothing is meant to change.

All files are 16 kHz, mono, 16 bit, which is what LVA captures, with one second of silence before and after.
"""

# pylint: disable=import-error,no-member

import sys
import wave
from pathlib import Path
from typing import Dict

import numpy as np

SAMPLE_RATE = 16000
SILENCE_SECONDS = 1.0
FIXTURES_DIR = Path(__file__).parent / "fixtures"

#: Marker tone frequencies, they must match tests/e2e/audio.py
PROBE_TONE_HZ = 1000
TTS_TONE_HZ = 1500
FIRST_MUSIC_TONE_HZ = 800
SECOND_MUSIC_TONE_HZ = 600

SPEECH: Dict[str, str] = {
    "okay_nabu": "okay nabu",
    "hey_jarvis": "hey jarvis",
    "stop": "stop",
    "unrelated_speech": "the weather is nice today and I would like a cup of coffee",
}


def main() -> None:
    FIXTURES_DIR.mkdir(exist_ok=True)

    if len(sys.argv) > 1:
        from piper import PiperVoice  # type: ignore[import-not-found]  # pylint: disable=import-outside-toplevel

        voice = PiperVoice.load(sys.argv[1])
        for name, text in SPEECH.items():
            chunks = [np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16) for chunk in voice.synthesize(text)]
            _write(name, _resample(np.concatenate(chunks), voice.config.sample_rate))

    _write("probe_tone", _tone(PROBE_TONE_HZ, 1.5))
    _write("tts_marker", _tone(TTS_TONE_HZ, 2.0), silence=False)
    _write("tts_marker_long", _tone(TTS_TONE_HZ, 12.0), silence=False)
    _write("music_first", _tone(FIRST_MUSIC_TONE_HZ, 30.0), silence=False)
    _write("music_second", _tone(SECOND_MUSIC_TONE_HZ, 30.0), silence=False)


def _resample(samples: np.ndarray, source_rate: int) -> np.ndarray:
    target_length = int(len(samples) * SAMPLE_RATE / source_rate)
    positions = np.linspace(0, len(samples) - 1, target_length)
    return np.interp(positions, np.arange(len(samples)), samples.astype(np.float32)).astype(np.int16)


def _tone(frequency: int, seconds: float) -> np.ndarray:
    t = np.arange(int(SAMPLE_RATE * seconds)) / SAMPLE_RATE
    fade = np.minimum(1.0, np.minimum(t, seconds - t) / 0.05)
    return (np.sin(2 * np.pi * frequency * t) * fade * 20000).astype(np.int16)


def _write(name: str, samples: np.ndarray, silence: bool = True) -> None:
    if silence:
        pad = np.zeros(int(SAMPLE_RATE * SILENCE_SECONDS), dtype=np.int16)
        samples = np.concatenate([pad, samples, pad])
    with wave.open(str(FIXTURES_DIR / f"{name}.wav"), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(samples.tobytes())


if __name__ == "__main__":
    main()
