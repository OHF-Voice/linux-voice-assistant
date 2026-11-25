"""What Home Assistant sees of LVA over the ESPHome API, and what its commands do."""

import asyncio
from pathlib import Path

from .audio import TTS_TONE_HZ, SpeakerRecorder, play_into_mic
from .fake_ha import FakeHomeAssistant, FixtureServer
from .lva import LvaProcess
from .waiting import wait_until

FIXTURES_DIR = Path(__file__).parent / "fixtures"

EXPECTED_ENTITIES = [
    "Media Player",
    "Mute",
    "Thinking Sound",
    "Wake Word 1 Sensitivity",
    "Wake Word 2 Sensitivity",
    "Stop Word Sensitivity",
    "Mic Auto Gain",
    "Mic Noise Suppression",
    "Mic Volume",
]


async def test_device_identity_and_entities(ha: FakeHomeAssistant) -> None:
    info = await ha.device_info()

    assert info.mac_address == "02:00:00:00:00:01"
    assert info.name == "lva-020000000001"
    assert info.friendly_name == "e2e"
    assert info.voice_assistant_feature_flags != 0
    assert [entity.name for entity in ha.entities] == EXPECTED_ENTITIES


async def test_mute_switch_blocks_wake_word(lva: LvaProcess, ha: FakeHomeAssistant) -> None:
    log = lva.log_mark()
    mute = ha.entity_by_name("Mute")

    ha.client.switch_command(mute.key, True)
    await wait_until(lambda: mute.key in ha.states and ha.states[mute.key].state is True, description="mute switch on")
    await play_into_mic(FIXTURES_DIR / "okay_nabu.wav")
    await asyncio.sleep(1.0)
    assert not ha.starts, "a muted assistant must not react to the wake word"

    ha.client.switch_command(mute.key, False)
    await wait_until(lambda: ha.states[mute.key].state is False, description="mute switch off")
    await asyncio.sleep(2.5)
    await play_into_mic(FIXTURES_DIR / "okay_nabu.wav")
    await ha.wait_for_start(1, timeout=10)
    ha.run_pipeline("never mind", None, None)
    await lva.wait_for_log_line("TTS response finished", log)


async def test_volume_command_changes_media_player_volume(ha: FakeHomeAssistant) -> None:
    media_player = ha.entity_by_name("Media Player")

    ha.client.media_player_command(media_player.key, volume=0.3)
    await wait_until(lambda: media_player.key in ha.states and abs(ha.states[media_player.key].volume - 0.3) < 0.01, description="volume 0.3")

    ha.client.media_player_command(media_player.key, volume=1.0)
    await wait_until(lambda: abs(ha.states[media_player.key].volume - 1.0) < 0.01, description="volume 1.0")


async def test_announcement_is_played_and_confirmed(ha: FakeHomeAssistant, speaker: SpeakerRecorder, media_server: FixtureServer) -> None:
    speaker_mark = speaker.mark()

    finished = await ha.announce(f"{media_server.base_url}/tts_marker.wav", "Dinner is ready")

    assert await speaker.wait_for_tone(speaker_mark, TTS_TONE_HZ, timeout=5), "the announcement was not played"
    assert finished is not None, "LVA did not confirm the announcement"
