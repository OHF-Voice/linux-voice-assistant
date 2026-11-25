"""Music from Home Assistant or Music Assistant: play, pause, skip, announce over it and duck it."""

import asyncio
from pathlib import Path

from aioesphomeapi import MediaPlayerCommand, MediaPlayerState

from .audio import FIRST_MUSIC_TONE_HZ, SECOND_MUSIC_TONE_HZ, TTS_TONE_HZ, SpeakerRecorder, play_into_mic
from .fake_ha import FakeHomeAssistant, FixtureServer
from .lva import LvaProcess

FIXTURES_DIR = Path(__file__).parent / "fixtures"


async def test_media_player_plays_and_stops_music(ha: FakeHomeAssistant, speaker: SpeakerRecorder, media_server: FixtureServer) -> None:
    ha.media_player_command(url=f"{media_server.base_url}/music_first.wav")

    assert await speaker.wait_for_tone(speaker.mark(), FIRST_MUSIC_TONE_HZ, timeout=8), "the music was not played"
    await ha.wait_for_media_state(MediaPlayerState.PLAYING)

    ha.media_player_command(command=MediaPlayerCommand.STOP)

    await ha.wait_for_media_state(MediaPlayerState.IDLE)
    await asyncio.sleep(0.5)
    assert not await speaker.wait_for_tone(speaker.mark(), FIRST_MUSIC_TONE_HZ, timeout=1), "the music kept playing after stop"


async def test_media_player_pauses_and_resumes_music(ha: FakeHomeAssistant, speaker: SpeakerRecorder, media_server: FixtureServer) -> None:
    ha.media_player_command(url=f"{media_server.base_url}/music_first.wav")
    assert await speaker.wait_for_tone(speaker.mark(), FIRST_MUSIC_TONE_HZ, timeout=8)

    ha.media_player_command(command=MediaPlayerCommand.PAUSE)

    await ha.wait_for_media_state(MediaPlayerState.PAUSED)
    await asyncio.sleep(0.5)
    assert not await speaker.wait_for_tone(speaker.mark(), FIRST_MUSIC_TONE_HZ, timeout=1), "the music kept playing while paused"

    ha.media_player_command(command=MediaPlayerCommand.PLAY)

    await ha.wait_for_media_state(MediaPlayerState.PLAYING)
    assert await speaker.wait_for_tone(speaker.mark(), FIRST_MUSIC_TONE_HZ, timeout=5), "the music did not continue after resume"
    ha.media_player_command(command=MediaPlayerCommand.STOP)
    await ha.wait_for_media_state(MediaPlayerState.IDLE)


async def test_next_track_replaces_the_current_one(ha: FakeHomeAssistant, speaker: SpeakerRecorder, media_server: FixtureServer) -> None:
    ha.media_player_command(url=f"{media_server.base_url}/music_first.wav")
    assert await speaker.wait_for_tone(speaker.mark(), FIRST_MUSIC_TONE_HZ, timeout=8)

    ha.media_player_command(url=f"{media_server.base_url}/music_second.wav")

    assert await speaker.wait_for_tone(speaker.mark(), SECOND_MUSIC_TONE_HZ, timeout=8), "the next track was not played"
    await asyncio.sleep(0.5)
    assert not await speaker.wait_for_tone(speaker.mark(), FIRST_MUSIC_TONE_HZ, timeout=1), "the previous track kept playing"
    ha.media_player_command(command=MediaPlayerCommand.STOP)
    await ha.wait_for_media_state(MediaPlayerState.IDLE)


async def test_text_to_speech_over_music_pauses_and_resumes_the_music(ha: FakeHomeAssistant, speaker: SpeakerRecorder, media_server: FixtureServer) -> None:
    ha.media_player_command(url=f"{media_server.base_url}/music_first.wav")
    assert await speaker.wait_for_tone(speaker.mark(), FIRST_MUSIC_TONE_HZ, timeout=8)
    mark = speaker.mark()

    ha.media_player_command(url=f"{media_server.base_url}/tts_marker.wav", announcement=True)

    assert await speaker.wait_for_tone(mark, TTS_TONE_HZ, timeout=5), "tts.speak was not played"
    await asyncio.sleep(2.5)
    assert await speaker.wait_for_tone(speaker.mark(), FIRST_MUSIC_TONE_HZ, timeout=5), "the music did not resume after tts.speak"
    ha.media_player_command(command=MediaPlayerCommand.STOP)
    await ha.wait_for_media_state(MediaPlayerState.IDLE)


async def test_music_is_ducked_while_assistant_listens(lva: LvaProcess, ha: FakeHomeAssistant, speaker: SpeakerRecorder, media_server: FixtureServer) -> None:
    ha.media_player_command(url=f"{media_server.base_url}/music_first.wav")
    assert await speaker.wait_for_tone(speaker.mark(), FIRST_MUSIC_TONE_HZ, timeout=8)
    await asyncio.sleep(1.0)
    normal = speaker.recent_amplitude(FIRST_MUSIC_TONE_HZ)

    log = lva.log_mark()
    await play_into_mic(FIXTURES_DIR / "okay_nabu.wav")
    await ha.wait_for_start(1, timeout=10)
    await asyncio.sleep(1.5)
    ducked = speaker.recent_amplitude(FIRST_MUSIC_TONE_HZ)

    ha.run_pipeline("never mind", None, None)
    await lva.wait_for_log_line("TTS response finished", log)
    await asyncio.sleep(1.5)
    restored = speaker.recent_amplitude(FIRST_MUSIC_TONE_HZ)
    ha.media_player_command(command=MediaPlayerCommand.STOP)

    assert normal > 1000
    assert ducked < normal * 0.75, f"music not ducked: {normal:.0f} -> {ducked:.0f}"
    assert restored > normal * 0.85, f"music not restored: {normal:.0f} -> {restored:.0f}"
