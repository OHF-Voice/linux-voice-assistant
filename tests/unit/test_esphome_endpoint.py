"""Unit tests for EsphomeEndpoint."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from linux_voice_assistant.esphome_endpoint import EsphomeEndpoint

OLD = "192.168.1.10"
NEW = "192.168.1.20"


def make_endpoint(*, discovery=True, listen_error=None):
    state = MagicMock(ip_address=OLD)
    server, new_server = MagicMock(), MagicMock()
    mdns = AsyncMock() if discovery else None
    listen = AsyncMock(return_value=new_server, side_effect=listen_error)
    endpoint = EsphomeEndpoint(state=state, bound_ip=OLD, server=server, discovery=mdns, listen=listen)
    return endpoint, state, server, new_server, mdns, listen


class TestApply:
    async def test_new_address_moves_listener_and_announcement(self):
        endpoint, state, server, _new_server, mdns, listen = make_endpoint()
        await endpoint.apply(NEW)
        listen.assert_awaited_once_with(NEW)
        server.close.assert_called_once()
        mdns.update_address.assert_awaited_once_with(NEW)
        assert state.ip_address == NEW

    async def test_new_listener_is_bound_before_the_old_one_is_closed(self):
        endpoint, _state, server, _new_server, _mdns, listen = make_endpoint()
        order = []

        async def record_listen(_ip):
            order.append("listen")
            return MagicMock()

        listen.side_effect = record_listen
        server.close.side_effect = lambda: order.append("close")
        await endpoint.apply(NEW)
        assert order == ["listen", "close"]

    async def test_failed_bind_changes_nothing(self):
        endpoint, state, server, _new_server, mdns, _listen = make_endpoint(listen_error=OSError("cannot assign"))
        with pytest.raises(OSError):
            await endpoint.apply(NEW)
        server.close.assert_not_called()
        mdns.update_address.assert_not_awaited()
        assert state.ip_address == OLD

    async def test_retry_after_failed_announcement_does_not_bind_again(self):
        endpoint, _state, _server, _new_server, mdns, listen = make_endpoint()
        mdns.update_address.side_effect = [OSError("no route"), None]
        with pytest.raises(OSError):
            await endpoint.apply(NEW)
        await endpoint.apply(NEW)
        listen.assert_awaited_once_with(NEW)
        assert mdns.update_address.await_count == 2

    async def test_same_address_keeps_the_listener(self):
        endpoint, state, server, _new_server, mdns, listen = make_endpoint()
        await endpoint.apply(OLD)
        listen.assert_not_awaited()
        server.close.assert_not_called()
        mdns.update_address.assert_awaited_once_with(OLD)
        assert state.ip_address == OLD

    async def test_without_mdns_only_the_listener_moves(self):
        endpoint, _state, server, _new_server, _mdns, listen = make_endpoint(discovery=False)
        await endpoint.apply(NEW)
        listen.assert_awaited_once_with(NEW)
        server.close.assert_called_once()


class TestClose:
    async def test_closes_the_current_server(self):
        endpoint, _state, server, _new_server, _mdns, _listen = make_endpoint()
        await endpoint.close()
        server.close.assert_called_once()

    async def test_closes_the_moved_server_after_a_rebind(self):
        endpoint, _state, server, new_server, _mdns, _listen = make_endpoint()
        await endpoint.apply(NEW)
        await endpoint.close()
        assert server.close.call_count == 1
        new_server.close.assert_called_once()
