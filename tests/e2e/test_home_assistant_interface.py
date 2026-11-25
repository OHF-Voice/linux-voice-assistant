"""What Home Assistant sees of LVA over the ESPHome API, and what its commands do."""

import asyncio
from pathlib import Path

import pytest
from aioesphomeapi import MediaPlayerCommand, MediaPlayerState

from .audio import MUSIC_TONE_HZ, TTS_TONE_HZ, SpeakerRecorder, play_into_mic, tone_amplitude
from .fake_ha import FakeHomeAssistant, FixtureServer
from .peripheral import PeripheralClient

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


@pytest.mark.usefixtures("ha")
async def test_peripheral_snapshot_reports_home_assistant_connection(peripheral: PeripheralClient) -> None:
    assert peripheral.snapshot["ha_connected"] is True
    assert peripheral.snapshot["muted"] is False
    assert 0.0 <= peripheral.snapshot["volume"] <= 1.0


async def test_mute_switch_blocks_wake_word(ha: FakeHomeAssistant, peripheral: PeripheralClient) -> None:
    events = peripheral.mark()
    mute = ha.entity_by_name("Mute")

    ha.client.switch_command(mute.key, True)
    muted = await peripheral.wait_for("muted", events)
    assert muted["data"]["muted"] is True
    await play_into_mic(FIXTURES_DIR / "okay_nabu.wav")
    await asyncio.sleep(1.0)
    assert not ha.starts, "a muted assistant must not react to the wake word"

    ha.client.switch_command(mute.key, False)
    await asyncio.sleep(2.5)
    await play_into_mic(FIXTURES_DIR / "okay_nabu.wav")
    await ha.wait_for_start(1, timeout=10)
    ha.run_pipeline("never mind", None, None)
    await peripheral.wait_for("idle", events)


async def test_volume_command_reaches_home_assistant(ha: FakeHomeAssistant, peripheral: PeripheralClient) -> None:
    events = peripheral.mark()
    media_player = ha.entity_by_name("Media Player")

    await peripheral.command("set_volume", {"volume": 0.3})

    changed = await peripheral.wait_for("volume_changed", events)
    assert abs(changed["data"]["volume"] - 0.3) < 0.01
    await _wait_until(lambda: media_player.key in ha.states and abs(ha.states[media_player.key].volume - 0.3) < 0.01)

    await peripheral.command("set_volume", {"volume": 1.0})
    await peripheral.wait_for("volume_changed", events + 1)


async def test_announcement_is_played_and_confirmed(ha: FakeHomeAssistant, speaker: SpeakerRecorder, media_server: FixtureServer) -> None:
    speaker_mark = speaker.mark()

    await ha.announce(f"{media_server.base_url}/tts_marker.wav", "Dinner is ready")

    assert await speaker.wait_for_tone(speaker_mark, TTS_TONE_HZ, timeout=5), "the announcement was not played"
    assert ha.announce_finished >= 1


async def test_media_player_plays_and_stops_music(ha: FakeHomeAssistant, peripheral: PeripheralClient, speaker: SpeakerRecorder, media_server: FixtureServer) -> None:
    events = peripheral.mark()
    media_player = ha.entity_by_name("Media Player")

    ha.client.media_player_command(media_player.key, media_url=f"{media_server.base_url}/music_marker.wav")

    await peripheral.wait_for("media_player_playing", events)
    assert await speaker.wait_for_tone(speaker.mark(), MUSIC_TONE_HZ, timeout=8), "the music was not played"
    await _wait_until(lambda: ha.states.get(media_player.key) is not None and ha.states[media_player.key].state == MediaPlayerState.PLAYING)

    ha.client.media_player_command(media_player.key, command=MediaPlayerCommand.STOP)

    await _wait_until(lambda: ha.states[media_player.key].state == MediaPlayerState.IDLE)
    await asyncio.sleep(0.5)
    assert not await speaker.wait_for_tone(speaker.mark(), MUSIC_TONE_HZ, timeout=1), "the music kept playing after stop"


async def test_music_is_ducked_while_assistant_listens(ha: FakeHomeAssistant, peripheral: PeripheralClient, speaker: SpeakerRecorder, media_server: FixtureServer) -> None:
    events = peripheral.mark()
    media_player = ha.entity_by_name("Media Player")
    ha.client.media_player_command(media_player.key, media_url=f"{media_server.base_url}/music_marker.wav")
    await peripheral.wait_for("media_player_playing", events)
    assert await speaker.wait_for_tone(speaker.mark(), MUSIC_TONE_HZ, timeout=8)
    await asyncio.sleep(1.0)
    normal = _recent_amplitude(speaker, MUSIC_TONE_HZ)

    await play_into_mic(FIXTURES_DIR / "okay_nabu.wav")
    await ha.wait_for_start(1, timeout=10)
    await peripheral.wait_for("listening", events)
    await asyncio.sleep(1.5)
    ducked = _recent_amplitude(speaker, MUSIC_TONE_HZ)

    ha.run_pipeline("never mind", None, None)
    await peripheral.wait_for("idle", events)
    await asyncio.sleep(1.5)
    restored = _recent_amplitude(speaker, MUSIC_TONE_HZ)
    ha.client.media_player_command(media_player.key, command=MediaPlayerCommand.STOP)

    assert normal > 1000
    assert ducked < normal * 0.75, f"music not ducked: {normal:.0f} -> {ducked:.0f}"
    assert restored > normal * 0.85, f"music not restored: {normal:.0f} -> {restored:.0f}"


def _recent_amplitude(speaker: SpeakerRecorder, frequency: int, seconds: float = 0.5) -> float:
    samples = speaker.samples_since(0)
    return tone_amplitude(samples[-int(16000 * seconds) :], frequency)


async def _wait_until(condition, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        if loop.time() >= deadline:
            raise TimeoutError("condition not met in time")
        await asyncio.sleep(0.05)
