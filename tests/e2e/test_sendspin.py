"""A Sendspin player next to LVA: how each --sendspin-player-duck-mode ducks it while the assistant listens."""

import asyncio
from pathlib import Path

import pytest

from .audio import play_into_mic
from .conftest import SETTLE_SECONDS, SendspinSetup
from .fake_ha import FakeHomeAssistant
from .waiting import wait_until

FIXTURES_DIR = Path(__file__).parent / "fixtures"


async def test_stop_mode_stops_sendspin_while_assistant_listens(sendspin_stop: SendspinSetup) -> None:
    """In stop mode a playing Sendspin player is stopped when the assistant starts listening and started again when the conversation is over."""
    await _converse(sendspin_stop, ducked=["stop"], restored=["stop", "play"])


async def test_duck_mode_lowers_the_sendspin_group_volume_while_assistant_listens(sendspin_duck: SendspinSetup) -> None:
    """In duck mode the group volume of a playing Sendspin player is lowered to 50 % while the assistant listens and restored afterwards."""
    await _converse(sendspin_duck, ducked=["vol 40"], restored=["vol 40", "vol 80"])


@pytest.mark.skip(reason="needs a sound stream named sendspin-cli in the test environment, see #639")
async def test_pulse_mode_lowers_the_sendspin_stream_while_assistant_listens() -> None:
    """In pulse mode (the default) the stream of a playing Sendspin player is lowered in the sound server while the assistant listens and restored afterwards."""


async def _converse(setup: SendspinSetup, ducked: list, restored: list) -> None:
    """Wake LVA, check the commands sent to the player while it listens and after the conversation."""
    ha = FakeHomeAssistant()
    await ha.connect(setup.lva.api_port)
    try:
        await asyncio.sleep(SETTLE_SECONDS)
        await wait_until(lambda: setup.player.polled, timeout=10, description="LVA reading the status of the Sendspin player")
        await wait_until(lambda: setup.player.volume == 80 and not setup.player.commands(), timeout=1, description="no command before the wake word")

        log = setup.lva.log_mark()
        await play_into_mic(FIXTURES_DIR / "okay_nabu.wav")
        await ha.wait_for_start(1, timeout=10)
        await wait_until(lambda: setup.player.commands() == ducked, timeout=5, description=f"{ducked} while the assistant listens, got {setup.player.commands()}")

        ha.run_pipeline("never mind", None, None)
        await setup.lva.wait_for_log_line("TTS response finished", log)
        await wait_until(lambda: setup.player.commands() == restored, timeout=5, description=f"{restored} after the conversation, got {setup.player.commands()}")
    finally:
        await ha.disconnect()
