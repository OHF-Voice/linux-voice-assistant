"""Tests for ducking the sound stream of an application in PulseAudio/PipeWire."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from linux_voice_assistant.pulse_stream_ducker import (
    PulseStreamDucker,
    StreamDuck,
    StreamPlan,
    StreamVolume,
    is_application_stream,
    plan_stream_volumes,
)

# ---------------------------------------------------------------------------
# is_application_stream
# ---------------------------------------------------------------------------


def test_native_output_is_found_by_application_name():
    assert is_application_stream({"application.name": "sendspin-cli"}, "sendspin-cli")


def test_alsa_plugin_output_is_found_by_process_binary():
    assert is_application_stream({"application.name": "ALSA plug-in [sendspin-cli]", "application.process.binary": "sendspin-cli"}, "sendspin-cli")


def test_other_streams_are_not_sendspin():
    assert not is_application_stream({"application.name": "mpv Media Player", "application.process.binary": "python3"}, "sendspin-cli")
    assert not is_application_stream({}, "sendspin-cli")


# ---------------------------------------------------------------------------
# plan_stream_volumes
# ---------------------------------------------------------------------------


def test_ducking_lowers_the_stream_and_remembers_the_volume():
    plan = plan_stream_volumes([StreamVolume(index=7, volume=0.8)], duck=None, wanted=True, factor=0.5)
    assert plan == StreamPlan(changes=((7, 0.4),), duck=StreamDuck(original=0.8, ducked=0.4))


def test_ducking_without_stream_does_nothing():
    assert plan_stream_volumes([], duck=None, wanted=True, factor=0.5) == StreamPlan(changes=(), duck=None)


def test_ducked_stream_is_not_lowered_again():
    duck = StreamDuck(original=0.8, ducked=0.4)
    assert plan_stream_volumes([StreamVolume(index=7, volume=0.4)], duck=duck, wanted=True, factor=0.5) == StreamPlan(changes=(), duck=duck)


def test_new_stream_at_the_original_volume_is_lowered():
    duck = StreamDuck(original=0.8, ducked=0.4)
    plan = plan_stream_volumes([StreamVolume(index=7, volume=0.4), StreamVolume(index=9, volume=0.8)], duck=duck, wanted=True, factor=0.5)
    assert plan.changes == ((9, 0.4),)


def test_stream_changed_by_someone_else_while_ducked_is_left_alone():
    duck = StreamDuck(original=0.8, ducked=0.4)
    assert plan_stream_volumes([StreamVolume(index=7, volume=0.6)], duck=duck, wanted=True, factor=0.5).changes == ()


def test_end_of_ducking_restores_the_stream():
    duck = StreamDuck(original=0.8, ducked=0.4)
    assert plan_stream_volumes([StreamVolume(index=7, volume=0.4)], duck=duck, wanted=False, factor=0.5) == StreamPlan(changes=((7, 0.8),), duck=None)


def test_end_of_ducking_leaves_a_changed_stream_alone():
    duck = StreamDuck(original=0.8, ducked=0.4)
    assert plan_stream_volumes([StreamVolume(index=7, volume=0.6)], duck=duck, wanted=False, factor=0.5) == StreamPlan(changes=(), duck=None)


def test_end_of_ducking_without_stream_keeps_the_ducking_for_a_later_stream():
    duck = StreamDuck(original=0.8, ducked=0.4)
    assert plan_stream_volumes([], duck=duck, wanted=False, factor=0.5) == StreamPlan(changes=(), duck=duck)
    assert plan_stream_volumes([StreamVolume(index=3, volume=0.4)], duck=duck, wanted=False, factor=0.5) == StreamPlan(changes=((3, 0.8),), duck=None)


def test_nothing_to_do_without_ducking():
    assert plan_stream_volumes([StreamVolume(index=7, volume=0.8)], duck=None, wanted=False, factor=0.5) == StreamPlan(changes=(), duck=None)


# ---------------------------------------------------------------------------
# PulseStreamDucker
# ---------------------------------------------------------------------------


class FakePulse:
    """Stands in for pulsectl_asyncio.PulseAsync: a list of sink inputs and the volumes set on them."""

    def __init__(self, sink_inputs):
        self.sink_inputs = sink_inputs
        self.set_volumes = []
        self.events = asyncio.Queue()

    async def sink_input_list(self):
        return self.sink_inputs

    async def volume_set_all_chans(self, sink_input, volume):
        self.set_volumes.append((sink_input.index, round(volume, 3)))
        sink_input.volume.value_flat = volume

    async def subscribe_events(self, *_masks):
        while True:
            event = await self.events.get()
            if isinstance(event, Exception):
                raise event
            yield event


def _sink_input(index, volume, name="sendspin-cli"):
    return SimpleNamespace(index=index, proplist={"application.name": name}, volume=SimpleNamespace(value_flat=volume))


async def test_apply_ducks_only_sendspin_streams_and_reports_the_ducking():
    pulse = FakePulse([_sink_input(1, 0.8), _sink_input(2, 1.0, name="mpv")])
    saved = MagicMock()
    ducker = PulseStreamDucker("sendspin-cli")
    ducker._on_duck_changed = saved  # pylint: disable=protected-access

    ducker.duck()
    await ducker._apply(pulse)  # pylint: disable=protected-access
    assert pulse.set_volumes == [(1, 0.4)]
    saved.assert_called_once_with(StreamDuck(original=0.8, ducked=0.4))

    ducker.unduck()
    await ducker._apply(pulse)  # pylint: disable=protected-access
    assert pulse.set_volumes == [(1, 0.4), (1, 0.8)]
    saved.assert_called_with(None)


async def test_apply_without_stream_logs_that_there_is_nothing_to_duck(caplog):
    pulse = FakePulse([_sink_input(2, 1.0, name="mpv")])
    ducker = PulseStreamDucker("sendspin-cli")
    ducker.duck()
    with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.pulse_stream_ducker"):
        await ducker._apply(pulse)  # pylint: disable=protected-access

    assert pulse.set_volumes == []
    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert [record.getMessage() for record in errors] == ["Cannot duck: no stream of sendspin-cli found in the sound server. Does it play through PulseAudio/PipeWire?"]


async def test_saved_ducking_of_an_earlier_run_is_restored():
    pulse = FakePulse([_sink_input(1, 0.4)])
    saved = MagicMock()
    ducker = PulseStreamDucker("sendspin-cli", saved=StreamDuck(original=0.8, ducked=0.4))
    ducker._on_duck_changed = saved  # pylint: disable=protected-access

    await ducker._apply(pulse)  # pylint: disable=protected-access

    assert pulse.set_volumes == [(1, 0.8)]
    saved.assert_called_once_with(None)


async def test_follow_reacts_to_requests_and_new_streams(caplog):
    pulse = FakePulse([])
    ducker = PulseStreamDucker("sendspin-cli")
    ducker._loop = asyncio.get_running_loop()  # pylint: disable=protected-access
    ducker._wake = asyncio.Event()  # pylint: disable=protected-access
    task = asyncio.create_task(ducker._follow(pulse))  # pylint: disable=protected-access
    await asyncio.sleep(0)

    with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.pulse_stream_ducker"):
        ducker.duck()
        await asyncio.sleep(0.01)
        assert pulse.set_volumes == []

        pulse.sink_inputs.append(_sink_input(5, 1.0))
        pulse.events.put_nowait(SimpleNamespace(t="new", index=5))
        await asyncio.sleep(0.01)
        assert pulse.set_volumes == [(5, 0.5)]

        ducker.unduck()
        await asyncio.sleep(0.01)
        assert pulse.set_volumes == [(5, 0.5), (5, 1.0)]

    assert "Stream 5 of sendspin-cli: volume 1.000 -> 0.500 (ducking requested)" in caplog.text
    assert "Stream 5 of sendspin-cli: volume 0.500 -> 1.000 (ducking ended)" in caplog.text

    pulse.events.put_nowait(ConnectionError("lost"))
    with pytest.raises(ConnectionError):
        await task


async def test_run_retries_and_warns_once_without_sound_server(monkeypatch, caplog):
    attempts = []

    class Unreachable:
        def __init__(self, *_args, **_kwargs):
            attempts.append(1)

        async def __aenter__(self):
            raise OSError("no server")

        async def __aexit__(self, *_args):
            return False

    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 3:
            raise asyncio.CancelledError

    monkeypatch.setattr("linux_voice_assistant.pulse_stream_ducker.pulsectl_asyncio.PulseAsync", Unreachable)
    monkeypatch.setattr("linux_voice_assistant.pulse_stream_ducker.asyncio.sleep", fake_sleep)

    with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.pulse_stream_ducker"):
        with pytest.raises(asyncio.CancelledError):
            await PulseStreamDucker("sendspin-cli").run()

    assert len(attempts) == 3
    assert sleeps == [1.0, 2.0, 4.0]
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "Cannot duck the stream of sendspin-cli, the sound server is not reachable (no server). Retrying" in warnings[0].getMessage()


async def test_start_runs_the_ducker_and_keeps_the_callback():
    ducker = PulseStreamDucker("sendspin-cli")
    saved = MagicMock()

    async def run():
        return None

    ducker.run = run  # type: ignore[method-assign]
    task = ducker.start(saved)
    await task
    assert ducker._on_duck_changed is saved  # pylint: disable=protected-access


async def test_apply_logs_all_streams_and_why_a_stream_is_left_alone(caplog):
    pulse = FakePulse([_sink_input(1, 0.6), _sink_input(2, 1.0, name="mpv")])
    ducker = PulseStreamDucker("sendspin-cli", saved=StreamDuck(original=0.8, ducked=0.4))
    ducker.duck()
    with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.pulse_stream_ducker"):
        await ducker._apply(pulse)  # pylint: disable=protected-access

    assert pulse.set_volumes == []
    assert "Ducking of sendspin-cli requested, 2 stream(s) in the sound server: 1 'sendspin-cli' (None) volume 0.600; 2 'mpv' (None) volume 1.000" in caplog.text
    assert "Streams of sendspin-cli: [1], remembered ducking: 0.800 -> 0.400, changes: none" in caplog.text
    assert "Stream 1 of sendspin-cli left at 0.600: not at the volume before ducking (0.800), changed by someone else" in caplog.text


async def test_apply_logs_the_remembered_ducking(caplog):
    pulse = FakePulse([_sink_input(1, 0.8)])
    ducker = PulseStreamDucker("sendspin-cli")
    ducker.duck()
    with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.pulse_stream_ducker"):
        await ducker._apply(pulse)  # pylint: disable=protected-access

    assert "Remembered ducking of sendspin-cli: none -> 0.800 -> 0.400" in caplog.text
    assert "Streams of sendspin-cli: [1], remembered ducking: none, changes: 1 -> 0.400" in caplog.text
