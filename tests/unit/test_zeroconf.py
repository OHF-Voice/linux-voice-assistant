"""Unit tests for HomeAssistantZeroconf."""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_zeroconf(**kwargs):
    defaults = dict(
        port=6053,
        mac_address="aa:bb:cc:dd:ee:ff",
        host_ip_address="192.168.1.100",
        name="lva-test",
    )
    defaults.update(kwargs)

    with patch("linux_voice_assistant.zeroconf.AsyncZeroconf") as mock_zc_cls:
        mock_zc = MagicMock()
        mock_zc_cls.return_value = mock_zc
        from linux_voice_assistant.zeroconf import HomeAssistantZeroconf
        instance = HomeAssistantZeroconf(**defaults)
        instance._mock_zc = mock_zc
        instance._mock_zc_cls = mock_zc_cls
        return instance


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestInit:
    def test_name_stored(self):
        zc = make_zeroconf(name="my-lva")
        assert zc.name == "my-lva"

    def test_port_stored(self):
        zc = make_zeroconf(port=1234)
        assert zc.port == 1234

    def test_mac_address_stored(self):
        zc = make_zeroconf(mac_address="11:22:33:44:55:66")
        assert zc.mac_address == "11:22:33:44:55:66"

    def test_host_ip_stored(self):
        zc = make_zeroconf(host_ip_address="10.0.0.1")
        assert zc.host_ip_address == "10.0.0.1"

    def test_name_defaults_to_mac_when_not_provided(self):
        with patch("linux_voice_assistant.zeroconf.AsyncZeroconf"):
            from linux_voice_assistant.zeroconf import HomeAssistantZeroconf
            zc = HomeAssistantZeroconf(
                port=6053,
                mac_address="aa:bb:cc:dd:ee:ff",
                host_ip_address="192.168.1.1",
                name=None,
            )
        assert zc.name == "aa:bb:cc:dd:ee:ff"

    def test_async_zeroconf_instantiated(self):
        with patch("linux_voice_assistant.zeroconf.AsyncZeroconf") as mock_cls:
            mock_cls.return_value = MagicMock()
            from linux_voice_assistant.zeroconf import HomeAssistantZeroconf
            HomeAssistantZeroconf(
                port=6053,
                mac_address="aa:bb:cc:dd:ee:ff",
                host_ip_address="192.168.1.1",
            )
            mock_cls.assert_called_once()


# ---------------------------------------------------------------------------
# register_server()
# ---------------------------------------------------------------------------


class TestRegisterServer:
    @pytest.mark.asyncio
    async def test_register_service_called(self):
        zc = make_zeroconf()
        zc._mock_zc.async_register_service = AsyncMock()

        with patch("linux_voice_assistant.zeroconf.AsyncServiceInfo"):
            await zc.register_server()

        zc._mock_zc.async_register_service.assert_called_once()

    @pytest.mark.asyncio
    async def test_service_name_contains_device_name(self):
        zc = make_zeroconf(name="lva-aabbccddee")
        zc._mock_zc.async_register_service = AsyncMock()

        captured = {}

        with patch("linux_voice_assistant.zeroconf.AsyncServiceInfo") as mock_info_cls:
            mock_info_cls.side_effect = lambda *a, **kw: captured.update({"args": a, "kwargs": kw}) or MagicMock()
            await zc.register_server()

        # First positional arg is service type, second is full service name
        assert "lva-aabbccddee" in captured["args"][1]

    @pytest.mark.asyncio
    async def test_service_type_is_esphomelib(self):
        zc = make_zeroconf()
        zc._mock_zc.async_register_service = AsyncMock()

        captured = {}

        with patch("linux_voice_assistant.zeroconf.AsyncServiceInfo") as mock_info_cls:
            mock_info_cls.side_effect = lambda *a, **kw: captured.update({"args": a, "kwargs": kw}) or MagicMock()
            await zc.register_server()

        assert captured["args"][0] == "_esphomelib._tcp.local."

    @pytest.mark.asyncio
    async def test_service_properties_contain_mac(self):
        zc = make_zeroconf(mac_address="aa:bb:cc:dd:ee:ff")
        zc._mock_zc.async_register_service = AsyncMock()

        captured = {}

        with patch("linux_voice_assistant.zeroconf.AsyncServiceInfo") as mock_info_cls:
            mock_info_cls.side_effect = lambda *a, **kw: captured.update({"args": a, "kwargs": kw}) or MagicMock()
            await zc.register_server()

        props = captured["kwargs"].get("properties", {})
        assert "mac" in props
        assert props["mac"] == "aa:bb:cc:dd:ee:ff"

    @pytest.mark.asyncio
    async def test_service_properties_contain_version(self):
        zc = make_zeroconf()
        zc._mock_zc.async_register_service = AsyncMock()

        captured = {}

        with patch("linux_voice_assistant.zeroconf.AsyncServiceInfo") as mock_info_cls:
            mock_info_cls.side_effect = lambda *a, **kw: captured.update({"args": a, "kwargs": kw}) or MagicMock()
            await zc.register_server()

        props = captured["kwargs"].get("properties", {})
        assert "version" in props

    @pytest.mark.asyncio
    async def test_service_port_matches(self):
        zc = make_zeroconf(port=9999)
        zc._mock_zc.async_register_service = AsyncMock()

        captured = {}

        with patch("linux_voice_assistant.zeroconf.AsyncServiceInfo") as mock_info_cls:
            mock_info_cls.side_effect = lambda *a, **kw: captured.update({"args": a, "kwargs": kw}) or MagicMock()
            await zc.register_server()

        assert captured["kwargs"].get("port") == 9999

    @pytest.mark.asyncio
    async def test_service_address_matches_host_ip(self):
        import socket
        zc = make_zeroconf(host_ip_address="10.0.0.5")
        zc._mock_zc.async_register_service = AsyncMock()

        captured = {}

        with patch("linux_voice_assistant.zeroconf.AsyncServiceInfo") as mock_info_cls:
            mock_info_cls.side_effect = lambda *a, **kw: captured.update({"args": a, "kwargs": kw}) or MagicMock()
            await zc.register_server()

        addresses = captured["kwargs"].get("addresses", [])
        assert socket.inet_aton("10.0.0.5") in addresses