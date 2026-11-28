"""
Ducks the sound stream of an application in PulseAudio/PipeWire (duck mode ``pulse``).

Used for a Sendspin player (``--sendspin-player-duck-mode pulse``).
LVA lowers the stream in the sound server and sends nothing to the Sendspin group. The Local Audio add-on notices the change of its
stream volume and passes it on to its group, so the other players of the group are ducked too. The stream is found by its application name. Pulse keeps the volume it last saw per application
(stream-restore), so a stream that starts while ducking is active, or after a restart while ducked, may already have the ducked volume.
That is why the volumes before and after ducking are kept until the stream is restored, also across restarts.
"""

import asyncio
import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple

import pulsectl_asyncio

_LOGGER = logging.getLogger(__name__)

SENDSPIN_APPLICATION_NAME = "sendspin-cli"
_VOLUME_TOLERANCE = 0.005
_RETRY_MIN_SECONDS = 1.0
_RETRY_MAX_SECONDS = 60.0


@dataclass(frozen=True)
class StreamVolume:
    """
    A sound stream of the player.
    :param index: Index of the stream (sink input) in the sound server.
    :param volume: Volume of the stream, 1.0 is 100 %.
    """

    index: int
    volume: float


@dataclass(frozen=True)
class StreamDuck:
    """
    The volumes of the stream before and after ducking.
    :param original: Volume before ducking, restored afterwards.
    :param ducked: Volume while ducked.
    """

    original: float
    ducked: float


@dataclass(frozen=True)
class StreamPlan:
    """
    What to do with the streams.
    :param changes: ``(stream index, new volume)`` for every stream that gets a new volume.
    :param duck: The ducking that is still to be restored afterwards, None if there is none.
    """

    changes: Tuple[Tuple[int, float], ...]
    duck: Optional[StreamDuck]


def is_application_stream(properties: Mapping[str, str], application_name: str) -> bool:
    """
    Whether a stream belongs to the application.
    :param properties: Properties of the stream (proplist).
    :param application_name: Name of the application. Compared with ``application.name`` (native PulseAudio/PipeWire output)
        and ``application.process.binary`` (output through the ALSA plugin, whose application name is ``ALSA plug-in [...]``).
    """
    return application_name in (properties.get("application.name"), properties.get("application.process.binary"))


def plan_stream_volumes(streams: Sequence[StreamVolume], duck: Optional[StreamDuck], wanted: bool, factor: float) -> StreamPlan:
    """
    Decide which streams of the application get which volume.
    :param streams: The streams of the application.
    :param duck: The ducking that is still to be restored, None if there is none.
    :param wanted: Whether ducking is requested.
    :param factor: Fraction of the volume while ducked, 0.0 to 1.0.
    :return: The changes and the ducking that is still to be restored.

    While ducking is requested, a stream at the volume before ducking is lowered. A stream at another volume is left alone: it already
    is ducked, or someone changed it. When ducking ends, every stream still at the ducked volume gets its volume back. If there is no
    stream at that moment, the ducking is kept, so that a stream that starts later at the ducked volume is restored.
    """
    if wanted:
        if duck is None:
            if not streams:
                return StreamPlan(changes=(), duck=None)
            original = streams[0].volume
            duck = StreamDuck(original=original, ducked=original * factor)
        changes = tuple((stream.index, duck.ducked) for stream in streams if _same_volume(stream.volume, duck.original) and not _same_volume(stream.volume, duck.ducked))
        return StreamPlan(changes=changes, duck=duck)

    if duck is None:
        return StreamPlan(changes=(), duck=None)

    changes = tuple((stream.index, duck.original) for stream in streams if _same_volume(stream.volume, duck.ducked))
    return StreamPlan(changes=changes, duck=duck if not streams else None)


class PulseStreamDucker:
    """
    Lowers the streams of an application in the sound server while ducking is requested.

    :meth:`duck` and :meth:`unduck` are thread-safe and only note the request. :meth:`run` does the work on the event loop: it connects to
    the sound server (``PULSE_SERVER``), applies the request and follows new streams. It never raises: without a sound server or without a
    stream of the application nothing happens.
    """

    def __init__(self, application_name: str, factor: float = 0.5, saved: Optional[StreamDuck] = None) -> None:
        """
        :param application_name: Name of the application whose streams are ducked, see :func:`is_application_stream`.
        :param factor: Fraction of the volume while ducked, 0.0 to 1.0.
        :param saved: A ducking that an earlier run did not restore, None if there is none.
        """
        self._application_name = application_name
        self._factor = factor
        self._duck = saved
        self._on_duck_changed: Optional[Callable[[Optional[StreamDuck]], None]] = None
        self._lock = threading.Lock()
        self._wanted = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._wake: Optional[asyncio.Event] = None

    def start(self, on_duck_changed: Callable[[Optional[StreamDuck]], None]) -> "asyncio.Task[None]":
        """
        Start :meth:`run` as a task. Must be called on the event loop.
        :param on_duck_changed: Called with the ducking that is still to be restored (to keep it across restarts), or None once it is restored.
        :return: The task, to be cancelled on shutdown.
        """
        self._on_duck_changed = on_duck_changed
        return asyncio.get_running_loop().create_task(self.run())

    @property
    def application_name(self) -> str:
        """Name of the application whose streams are ducked."""
        return self._application_name

    def duck(self) -> None:
        """Lower the streams of the application, also one that starts later, until :meth:`unduck`."""
        self._request(True)

    def unduck(self) -> None:
        """Give the streams of the application their volume back."""
        self._request(False)

    async def run(self) -> None:
        """Connect to the sound server and keep the streams of the application at the requested volume. Runs until cancelled."""
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        retry = _RETRY_MIN_SECONDS
        reported = False
        while True:
            try:
                async with pulsectl_asyncio.PulseAsync("linux-voice-assistant") as pulse:
                    _LOGGER.info("Connected to the sound server, ducking the stream of %s there", self._application_name)
                    _LOGGER.debug("Sound server for %s: PULSE_SERVER=%s", self._application_name, os.environ.get("PULSE_SERVER", "(not set, the default of libpulse)"))
                    retry = _RETRY_MIN_SECONDS
                    reported = False
                    await self._follow(pulse)
            except Exception as err:  # pylint: disable=broad-exception-caught
                if not reported:
                    _LOGGER.warning("Cannot duck the stream of %s, the sound server is not reachable (%s). Retrying", self._application_name, err)
                    reported = True
                else:
                    _LOGGER.debug("Sound server still not reachable for %s (%s), retrying in %.0f s", self._application_name, err, retry)
            await asyncio.sleep(retry)
            retry = min(retry * 2, _RETRY_MAX_SECONDS)

    async def _follow(self, pulse: Any) -> None:
        """Apply the request now, whenever it changes and whenever a stream starts."""
        assert self._wake is not None
        events = asyncio.create_task(self._wake_on_new_streams(pulse))
        try:
            while True:
                self._wake.clear()
                await self._apply(pulse)
                wake = asyncio.create_task(self._wake.wait())
                done, _ = await asyncio.wait({wake, events}, return_when=asyncio.FIRST_COMPLETED)
                if events in done:
                    wake.cancel()
                    events.result()
                    return
        finally:
            events.cancel()

    async def _wake_on_new_streams(self, pulse: Any) -> None:
        assert self._wake is not None
        async for event in pulse.subscribe_events("sink_input"):
            if event.t == "new":
                _LOGGER.debug("New stream %s in the sound server, checking it for %s", event.index, self._application_name)
                self._wake.set()

    async def _apply(self, pulse: Any) -> None:
        with self._lock:
            wanted = self._wanted

        all_inputs = await pulse.sink_input_list()
        _LOGGER.debug(
            "Ducking of %s %s, %d stream(s) in the sound server: %s",
            self._application_name,
            "requested" if wanted else "not requested",
            len(all_inputs),
            "; ".join(_describe_stream(item) for item in all_inputs) or "none",
        )
        sink_inputs = {item.index: item for item in all_inputs if is_application_stream(item.proplist, self._application_name)}
        streams = [StreamVolume(index=index, volume=item.volume.value_flat) for index, item in sink_inputs.items()]
        plan = plan_stream_volumes(streams, self._duck, wanted, self._factor)
        _LOGGER.debug(
            "Streams of %s: %s, remembered ducking: %s, changes: %s",
            self._application_name,
            sorted(sink_inputs) or "none",
            _describe_duck(self._duck),
            ", ".join(f"{index} -> {volume:.3f}" for index, volume in plan.changes) or "none",
        )
        changed = {index for index, _ in plan.changes}
        for stream in streams:
            if stream.index not in changed:
                _LOGGER.debug("Stream %d of %s left at %.3f: %s", stream.index, self._application_name, stream.volume, _unchanged_reason(stream.volume, plan.duck, wanted))
        if not streams and wanted:
            _LOGGER.error("Cannot duck: no stream of %s found in the sound server. Does it play through PulseAudio/PipeWire?", self._application_name)
        elif not streams:
            _LOGGER.debug("No stream of %s in the sound server (ducking ended), nothing to change", self._application_name)

        for index, volume in plan.changes:
            _LOGGER.debug("Stream %d of %s: volume %.3f -> %.3f (ducking %s)", index, self._application_name, sink_inputs[index].volume.value_flat, volume, "requested" if wanted else "ended")
            await pulse.volume_set_all_chans(sink_inputs[index], volume)

        if plan.duck != self._duck:
            _LOGGER.debug("Remembered ducking of %s: %s -> %s", self._application_name, _describe_duck(self._duck), _describe_duck(plan.duck))
            self._duck = plan.duck
            if self._on_duck_changed is not None:
                self._on_duck_changed(plan.duck)

    def _request(self, wanted: bool) -> None:
        with self._lock:
            self._wanted = wanted
        _LOGGER.debug("Stream ducking of %s %s", self._application_name, "requested" if wanted else "ended")
        if self._loop is not None and self._wake is not None:
            self._loop.call_soon_threadsafe(self._wake.set)


def _same_volume(first: float, second: float) -> bool:
    return abs(first - second) < _VOLUME_TOLERANCE


def _describe_stream(item: Any) -> str:
    properties = item.proplist
    return f"{item.index} {properties.get('application.name')!r} ({properties.get('application.process.binary')}) volume {item.volume.value_flat:.3f}"


def _describe_duck(duck: Optional[StreamDuck]) -> str:
    return "none" if duck is None else f"{duck.original:.3f} -> {duck.ducked:.3f}"


def _unchanged_reason(volume: float, duck: Optional[StreamDuck], wanted: bool) -> str:
    if duck is None:
        return "nothing to restore" if not wanted else "no ducking"
    if wanted:
        return "already ducked" if _same_volume(volume, duck.ducked) else f"not at the volume before ducking ({duck.original:.3f}), changed by someone else"
    return f"not at the ducked volume ({duck.ducked:.3f}), changed by someone else"
