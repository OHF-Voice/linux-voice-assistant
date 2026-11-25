"""Unit tests for the WebSocket server of the peripheral API (PeripheralAPIServer)."""

import asyncio
import logging
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from linux_voice_assistant.peripheral_api import LVAEvent, PeripheralAPIServer
from linux_voice_assistant.peripheral_api.v1 import PeripheralApiV1
from tests.unit.conftest import make_state


class TestInit:
    def test_default_host_and_port(self):
        server = PeripheralAPIServer()
        assert server._host == "0.0.0.0"
        assert server._port == 6055

    def test_custom_host_and_port(self):
        server = PeripheralAPIServer(host="127.0.0.1", port=9999)
        assert server._host == "127.0.0.1"
        assert server._port == 9999

    def test_default_volume_step_is_the_one_of_v1(self):
        assert PeripheralAPIServer.DEFAULT_VOLUME_STEP == PeripheralApiV1.DEFAULT_VOLUME_STEP

    def test_volume_step_reaches_v1(self):
        server = PeripheralAPIServer(volume_step=0.1)
        assert server._versions["v1"]._volume_step == pytest.approx(0.1)

    def test_v1_is_offered(self):
        assert "v1" in PeripheralAPIServer()._versions


class TestSetState:
    def test_attaches_state_to_every_version(self, tmp_path):
        state = make_state(tmp_path)
        server = PeripheralAPIServer()
        server.set_state(state)
        for handler in server._versions.values():
            assert handler._state is state


class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_creates_server(self):
        server = PeripheralAPIServer()
        fake_server = MagicMock()

        with patch("websockets.server.serve", AsyncMock(return_value=fake_server)) as mock_serve:
            await server.start()

        mock_serve.assert_called_once()
        assert server._server is fake_server

    @pytest.mark.asyncio
    async def test_start_uses_configured_host_and_port(self):
        server = PeripheralAPIServer(host="127.0.0.1", port=1234)

        with patch("websockets.server.serve", AsyncMock(return_value=MagicMock())) as mock_serve:
            await server.start()

        _, args, _ = mock_serve.mock_calls[0]
        assert args[1:] == ("127.0.0.1", 1234)

    @pytest.mark.asyncio
    async def test_start_refuses_unknown_versions_before_the_handshake(self):
        server = PeripheralAPIServer()

        with patch("websockets.server.serve", AsyncMock(return_value=MagicMock())) as mock_serve:
            await server.start()

        assert mock_serve.mock_calls[0].kwargs["process_request"].__name__ == "_process_request"

    @pytest.mark.asyncio
    async def test_start_sets_loop_for_every_version(self):
        server = PeripheralAPIServer()

        with patch("websockets.server.serve", AsyncMock(return_value=MagicMock())):
            await server.start()

        assert server._loop is asyncio.get_running_loop()
        assert server._versions["v1"]._loop is asyncio.get_running_loop()

    @pytest.mark.asyncio
    async def test_stop_closes_server(self):
        server = PeripheralAPIServer()
        fake_server = MagicMock()
        fake_server.wait_closed = AsyncMock()
        server._server = fake_server

        await server.stop()

        fake_server.close.assert_called_once()
        fake_server.wait_closed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_without_server_does_not_raise(self):
        await PeripheralAPIServer().stop()

    @pytest.mark.asyncio
    async def test_stop_stops_every_version(self):
        server = PeripheralAPIServer()
        handler = MagicMock()
        handler.stop = AsyncMock()
        server._versions = {"v1": handler}

        await server.stop()

        handler.stop.assert_awaited_once()


class TestHandleClient:
    @pytest.mark.asyncio
    async def test_no_path_goes_to_v1(self):
        server = PeripheralAPIServer()
        handler = MagicMock()
        handler.handle_client = AsyncMock()
        server._versions = {"v1": handler}
        websocket = MagicMock(path="/")

        await server._handle_client(websocket)

        handler.handle_client.assert_awaited_once_with(websocket)

    @pytest.mark.asyncio
    async def test_versioned_path_goes_to_that_version(self):
        server = PeripheralAPIServer()
        v1, v2 = MagicMock(), MagicMock()
        v1.handle_client, v2.handle_client = AsyncMock(), AsyncMock()
        server._versions = {"v1": v1, "v2": v2}
        websocket = MagicMock(path="/v2")

        await server._handle_client(websocket)

        v2.handle_client.assert_awaited_once_with(websocket)
        v1.handle_client.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_path_is_not_served(self):
        server = PeripheralAPIServer()
        handler = MagicMock()
        handler.handle_client = AsyncMock()
        server._versions = {"v1": handler}

        await server._handle_client(MagicMock(path="/v9"))

        handler.handle_client.assert_not_awaited()


class TestProcessRequest:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["/", "/v1"])
    async def test_supported_path_continues_the_handshake(self, path):
        assert await PeripheralAPIServer()._process_request(path, {}) is None

    @pytest.mark.asyncio
    async def test_unknown_version_gets_404_and_a_warning(self, caplog):
        server = PeripheralAPIServer()

        with caplog.at_level(logging.WARNING):
            response = await server._process_request("/v9", {})

        assert response is not None
        status, _, body = response
        assert status == HTTPStatus.NOT_FOUND
        assert b"v1" in body
        assert "unsupported API version '/v9'" in caplog.text
        assert "supported: v1" in caplog.text


class TestEmitEvent:
    @pytest.mark.asyncio
    async def test_event_is_offered_to_every_version(self):
        server = PeripheralAPIServer()
        v1, v2 = MagicMock(), MagicMock()
        v1.emit_event, v2.emit_event = AsyncMock(), AsyncMock()
        server._versions = {"v1": v1, "v2": v2}

        await server.emit_event(LVAEvent.IDLE, {"a": 1})

        v1.emit_event.assert_awaited_once_with(LVAEvent.IDLE, {"a": 1})
        v2.emit_event.assert_awaited_once_with(LVAEvent.IDLE, {"a": 1})


class TestEmitEventSync:
    def test_noop_when_no_loop(self):
        PeripheralAPIServer().emit_event_sync(LVAEvent.IDLE)  # should not raise

    def test_noop_when_loop_closed(self):
        server = PeripheralAPIServer()
        loop = MagicMock()
        loop.is_closed.return_value = True
        server._loop = loop

        with patch("asyncio.run_coroutine_threadsafe") as mock_run:
            server.emit_event_sync(LVAEvent.IDLE)

        mock_run.assert_not_called()

    def test_schedules_coroutine_on_loop(self):
        server = PeripheralAPIServer()
        loop = MagicMock()
        loop.is_closed.return_value = False
        server._loop = loop

        with patch("asyncio.run_coroutine_threadsafe") as mock_run:
            server.emit_event_sync(LVAEvent.IDLE, {"foo": "bar"})
            mock_run.call_args.args[0].close()

        mock_run.assert_called_once()
        assert mock_run.call_args.args[1] is loop
