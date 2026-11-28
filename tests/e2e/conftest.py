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
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Iterator

import pytest

from .audio import SpeakerRecorder, VirtualAudio
from .fake_ha import FakeHomeAssistant, FixtureServer
from .fake_sendspin import FakeSendspinPlayer
from .lva import LvaProcess

FIXTURES_DIR = Path(__file__).parent / "fixtures"
LVA_START_TIMEOUT = 180.0

#: Time after connecting before a test speaks, so a wake word of the previous test is out of its refractory period
SETTLE_SECONDS = 2.5


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    """Add the docstring of a test to the JUnit XML as its description, which the job summary shows next to the test. Runs before the fixtures, so a test whose setup fails has it too."""
    test_function = getattr(item, "function", None)
    description = (test_function.__doc__ or "").strip() if test_function is not None else ""
    if description:
        item.user_properties.append(("description", description))


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


@pytest.fixture(scope="session")
def auto_stop_lva(virtual_audio: VirtualAudio, tmp_path_factory: pytest.TempPathFactory) -> Iterator[LvaProcess]:
    """A third LVA whose ringing timer stops after 3 s, so a test can wait for the timer to stop on its own (the default is 900 s)."""
    process = LvaProcess(_free_port(), _free_port(), tmp_path_factory.mktemp("lva-auto-stop"), extra_args=["--timer-max-ring-seconds", "3"])
    process.start()
    try:
        if not process.wait_for_log("Server started", LVA_START_TIMEOUT):
            raise RuntimeError(f"LVA did not start:\n{process.full_log()}")
        yield process
    finally:
        process.stop()
        _store_log(process, "lva-auto-stop.log")


@dataclass
class SendspinSetup:
    """An LVA instance that controls a fake Sendspin player in one duck mode."""

    lva: LvaProcess
    player: FakeSendspinPlayer


@pytest.fixture(scope="session")
def sendspin_stop(virtual_audio: VirtualAudio, tmp_path_factory: pytest.TempPathFactory) -> Iterator[SendspinSetup]:
    """LVA with --sendspin-player-duck-mode stop and a fake Sendspin player that plays."""
    yield from _sendspin_setup("stop", tmp_path_factory)


@pytest.fixture(scope="session")
def sendspin_duck(virtual_audio: VirtualAudio, tmp_path_factory: pytest.TempPathFactory) -> Iterator[SendspinSetup]:
    """LVA with --sendspin-player-duck-mode duck and a fake Sendspin player that plays."""
    yield from _sendspin_setup("duck", tmp_path_factory)


@pytest.fixture
async def ha(lva: LvaProcess) -> AsyncIterator[FakeHomeAssistant]:
    home_assistant = FakeHomeAssistant()
    await home_assistant.connect(lva.api_port)
    await asyncio.sleep(SETTLE_SECONDS)
    yield home_assistant
    await home_assistant.disconnect()


def _sendspin_setup(duck_mode: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[SendspinSetup]:
    player = FakeSendspinPlayer(tmp_path_factory.mktemp(f"sendspin-{duck_mode}"))
    player.start()
    process = LvaProcess(
        _free_port(),
        _free_port(),
        tmp_path_factory.mktemp(f"lva-sendspin-{duck_mode}"),
        extra_args=["--sendspin-control-dir", str(player.directory), "--sendspin-player-duck-mode", duck_mode],
    )
    process.start()
    try:
        if not process.wait_for_log("Server started", LVA_START_TIMEOUT):
            raise RuntimeError(f"LVA did not start:\n{process.full_log()}")
        yield SendspinSetup(lva=process, player=player)
    finally:
        process.stop()
        player.stop()
        _store_log(process, f"lva-sendspin-{duck_mode}.log")


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
