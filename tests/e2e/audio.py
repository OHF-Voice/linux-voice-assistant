"""Virtual audio devices for the end-to-end tests, on top of a running PulseAudio or PipeWire-Pulse server.

Two null sinks stand in for the hardware:

* ``e2e_mic`` receives the test audio. LVA records from ``e2e_mic_source``, a remapped copy of its monitor.
  A remapped source is needed because LVA's audio library hides plain monitor sources.
* ``e2e_speaker`` is the output of LVA. A recorder on its monitor tells what LVA played.
"""

import asyncio
import subprocess
import threading
from pathlib import Path
from typing import List, Optional

import numpy as np

SAMPLE_RATE = 16000
BYTES_PER_SECOND = SAMPLE_RATE * 2

MIC_SINK = "e2e_mic"
MIC_SOURCE = "e2e_mic_source"
SPEAKER_SINK = "e2e_speaker"

#: Must match tests/e2e/generate_fixtures.py
PROBE_TONE_HZ = 1000
TTS_TONE_HZ = 1500
MUSIC_TONE_HZ = 800

_WINDOW_SECONDS = 0.2


class VirtualAudio:
    """Creates the virtual devices and unloads them again."""

    def __init__(self) -> None:
        self._module_ids: List[str] = []

    def create(self) -> None:
        self._load("module-null-sink", f"sink_name={MIC_SINK}", "sink_properties=device.description=E2E-Mic-Sink")
        self._load("module-remap-source", f"master={MIC_SINK}.monitor", f"source_name={MIC_SOURCE}", "source_properties=device.description=E2E-Mic")
        self._load("module-null-sink", f"sink_name={SPEAKER_SINK}", "sink_properties=device.description=E2E-Speaker")

    def destroy(self) -> None:
        for module_id in reversed(self._module_ids):
            subprocess.run(["pactl", "unload-module", module_id], check=False, capture_output=True)
        self._module_ids.clear()

    def _load(self, module: str, *arguments: str) -> None:
        result = subprocess.run(["pactl", "load-module", module, *arguments], check=True, capture_output=True, text=True)
        self._module_ids.append(result.stdout.strip())


async def play_into_mic(wav_path: Path) -> None:
    """
    Play a wav file onto the virtual microphone and wait until it was played completely.
    :param wav_path: 16 kHz mono wav file, see tests/e2e/fixtures.
    """
    process = await asyncio.create_subprocess_exec("paplay", f"--device={MIC_SINK}", str(wav_path))
    if await process.wait() != 0:
        raise RuntimeError(f"paplay failed for {wav_path}")


class SpeakerRecorder:
    """Records everything LVA plays, so tests can check for sound and for marker tones."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._process: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        # pylint: disable-next=consider-using-with
        self._process = subprocess.Popen(
            ["parec", f"--device={SPEAKER_SINK}.monitor", "--format=s16le", f"--rate={SAMPLE_RATE}", "--channels=1", "--latency-msec=20"],
            stdout=subprocess.PIPE,
        )
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._process is not None:
            self._process.terminate()
            self._process.wait()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def mark(self) -> int:
        """Return a position that later checks use as their start, so earlier sound is ignored."""
        with self._lock:
            return len(self._buffer)

    def samples_since(self, mark: int) -> np.ndarray:
        with self._lock:
            data = bytes(self._buffer[mark:])
        return np.frombuffer(data[: len(data) // 2 * 2], dtype=np.int16)

    async def wait_for_sound(self, mark: int, timeout: float) -> bool:
        """Wait until anything audible was played after the mark."""
        return await self._wait(mark, timeout, has_sound)

    async def wait_for_tone(self, mark: int, frequency: int, timeout: float) -> bool:
        """Wait until a tone of the given frequency was played after the mark."""
        return await self._wait(mark, timeout, lambda samples: has_tone(samples, frequency))

    async def _wait(self, mark: int, timeout: float, check) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            if check(self.samples_since(mark)):
                return True
            if loop.time() >= deadline:
                return False
            await asyncio.sleep(0.1)

    def _read(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        while True:
            chunk = self._process.stdout.read(1024)
            if not chunk:
                return
            with self._lock:
                self._buffer.extend(chunk)


def has_sound(samples: np.ndarray, min_rms: float = 300.0) -> bool:
    """Tell whether the samples contain audible sound in at least one window."""
    return any(_rms(window) >= min_rms for window in _windows(samples))


def has_tone(samples: np.ndarray, frequency: int, min_rms: float = 1000.0, min_share: float = 0.6) -> bool:
    """
    Tell whether the samples contain a tone of the frequency in at least one window.
    :param samples: 16 kHz mono samples.
    :param frequency: Frequency of the tone in Hz.
    :param min_rms: Minimum loudness of a window to count.
    :param min_share: Minimum share of the window's energy that lies at the frequency, so other sounds do not count.
    """
    for window in _windows(samples):
        rms = _rms(window)
        if rms >= min_rms and _tone_share(window, frequency) >= min_share:
            return True
    return False


def tone_amplitude(samples: np.ndarray, frequency: int) -> float:
    """Amplitude of the tone at the frequency, over the whole samples. 0.0 if there are no samples."""
    if len(samples) == 0:
        return 0.0
    x = samples.astype(np.float64)
    t = np.arange(len(x)) / SAMPLE_RATE
    return float(np.abs(np.sum(x * np.exp(-2j * np.pi * frequency * t))) * 2 / len(x))


def _windows(samples: np.ndarray) -> List[np.ndarray]:
    size = int(SAMPLE_RATE * _WINDOW_SECONDS)
    return [samples[start : start + size] for start in range(0, len(samples) - size + 1, size)]


def _rms(window: np.ndarray) -> float:
    return float(np.sqrt(np.mean(window.astype(np.float64) ** 2)))


def _tone_share(window: np.ndarray, frequency: int) -> float:
    """Share of the window's energy at the frequency, 1.0 for a pure tone (Goertzel)."""
    total = np.mean(window.astype(np.float64) ** 2)
    return float(tone_amplitude(window, frequency) ** 2 / 2 / total) if total > 0 else 0.0
