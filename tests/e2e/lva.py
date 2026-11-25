"""Runs LVA as a subprocess for the end-to-end tests, with the virtual audio devices and no real Home Assistant."""

import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional

from .audio import MIC_SOURCE, SPEAKER_SINK

REPO_DIR = Path(__file__).resolve().parents[2]


class LvaProcess:
    """One LVA instance. Its log is collected so tests can wait for and search log lines."""

    def __init__(self, api_port: int, peripheral_port: int, work_dir: Path, extra_args: Optional[List[str]] = None) -> None:
        self.api_port = api_port
        self.peripheral_port = peripheral_port
        self._command = [
            sys.executable,
            "-m",
            "linux_voice_assistant",
            "--debug",
            "--name",
            "e2e",
            "--host",
            "127.0.0.1",
            "--network-interface",
            "lo",
            "--mac-address",
            "02:00:00:00:00:01",
            "--port",
            str(api_port),
            "--enable-zeroconf",
            "0",
            "--peripheral-host",
            "127.0.0.1",
            "--peripheral-port",
            str(peripheral_port),
            "--peripheral-startup-wait",
            "0",
            "--audio-input-device",
            MIC_SOURCE,
            "--audio-output-device",
            f"pulse/{SPEAKER_SINK}",
            "--wake-word-root-dir",
            str(REPO_DIR / "wakewords"),
            "--preferences-file",
            str(work_dir / "preferences.json"),
            "--wake-model",
            "okay_nabu",
            "--refractory-seconds",
            "2",
            *(extra_args or []),
        ]
        self._lines: List[str] = []
        self._lock = threading.Lock()
        self._process: Optional[subprocess.Popen] = None

    def start(self) -> None:
        self._process = subprocess.Popen(self._command, cwd=REPO_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        threading.Thread(target=self._read, daemon=True).start()

    def stop(self) -> None:
        if self._process is None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def log_mark(self) -> int:
        with self._lock:
            return len(self._lines)

    def log_since(self, mark: int) -> str:
        with self._lock:
            return "\n".join(self._lines[mark:])

    def full_log(self) -> str:
        return self.log_since(0)

    def wait_for_log(self, text: str, timeout: float, since: int = 0) -> bool:
        """Wait until a log line contains the text."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if text in self.log_since(since):
                return True
            if not self.running:
                return text in self.log_since(since)
            time.sleep(0.1)
        return False

    def _read(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        for line in self._process.stdout:
            with self._lock:
                self._lines.append(line.rstrip("\n"))
