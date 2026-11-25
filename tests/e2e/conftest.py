"""Fixtures of the end-to-end tests.

One LVA instance runs for the whole session, with virtual audio devices and a fake Home Assistant that connects to it
for every test. See tests/e2e/README.md.
"""

# pylint: disable=unused-argument

import asyncio
import os
import shutil
import socket
import subprocess
from pathlib import Path
from typing import AsyncIterator, Iterator

import pytest

from .audio import SpeakerRecorder, VirtualAudio
from .fake_ha import FakeHomeAssistant, FixtureServer
from .lva import LvaProcess
from .peripheral import PeripheralClient

FIXTURES_DIR = Path(__file__).parent / "fixtures"
LVA_START_TIMEOUT = 90.0

#: Time after connecting before a test speaks, so a wake word of the previous test is out of its refractory period
SETTLE_SECONDS = 2.5


@pytest.fixture(scope="session")
def virtual_audio() -> Iterator[VirtualAudio]:
    missing = [tool for tool in ("pactl", "paplay", "parec") if shutil.which(tool) is None]
    if missing:
        pytest.skip(f"PulseAudio tools missing: {missing}")
    if subprocess.run(["pactl", "info"], check=False, capture_output=True).returncode != 0:
        pytest.skip("no PulseAudio or PipeWire-Pulse server is running")
    audio = VirtualAudio()
    audio.create()
    yield audio
    audio.destroy()


@pytest.fixture(scope="session")
def speaker(virtual_audio: VirtualAudio) -> Iterator[SpeakerRecorder]:
    recorder = SpeakerRecorder()
    recorder.start()
    yield recorder
    recorder.stop()


@pytest.fixture(scope="session")
def media_server() -> Iterator[FixtureServer]:
    server = FixtureServer(FIXTURES_DIR)
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="session")
def lva(virtual_audio: VirtualAudio, tmp_path_factory: pytest.TempPathFactory) -> Iterator[LvaProcess]:
    process = LvaProcess(_free_port(), _free_port(), tmp_path_factory.mktemp("lva"))
    process.start()
    try:
        if not process.wait_for_log("Server started", LVA_START_TIMEOUT):
            raise RuntimeError(f"LVA did not start:\n{process.full_log()}")
        yield process
    finally:
        process.stop()
        _store_log(process, "lva.log")


@pytest.fixture(scope="session")
def peripheral_lva(virtual_audio: VirtualAudio, tmp_path_factory: pytest.TempPathFactory) -> Iterator[LvaProcess]:
    """A second LVA for the peripheral API tests. Peripherals register entities, which changes what Home Assistant sees and reconnects it."""
    process = LvaProcess(_free_port(), _free_port(), tmp_path_factory.mktemp("lva-peripheral"))
    process.start()
    try:
        if not process.wait_for_log("Server started", LVA_START_TIMEOUT):
            raise RuntimeError(f"LVA did not start:\n{process.full_log()}")
        yield process
    finally:
        process.stop()
        _store_log(process, "lva-peripheral.log")


@pytest.fixture
async def ha(lva: LvaProcess) -> AsyncIterator[FakeHomeAssistant]:
    home_assistant = FakeHomeAssistant()
    await home_assistant.connect(lva.api_port)
    await asyncio.sleep(SETTLE_SECONDS)
    yield home_assistant
    await home_assistant.disconnect()


@pytest.fixture
async def peripheral(lva: LvaProcess) -> AsyncIterator[PeripheralClient]:
    client = PeripheralClient()
    await client.connect(lva.peripheral_port)
    yield client
    await client.disconnect()


def _store_log(process: LvaProcess, file_name: str) -> None:
    log_dir = os.environ.get("E2E_LOG_DIR")
    if log_dir:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        (Path(log_dir) / file_name).write_text(process.full_log(), encoding="utf-8")
    else:
        print(f"\n----- {file_name} -----\n" + process.full_log())


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
