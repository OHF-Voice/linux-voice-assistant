"""Unit tests for utility functions."""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


# ---------------------------------------------------------------------------
# get_version()
# ---------------------------------------------------------------------------


class TestGetVersion:
    def setup_method(self):
        """Reset the version cache before each test."""
        import linux_voice_assistant.util as util
        util._version_cache = None

    def test_returns_unknown_when_file_missing(self):
        import linux_voice_assistant.util as util
        util._version_cache = None

        with patch.object(Path, "read_text", side_effect=FileNotFoundError):
            result = util.get_version()
        assert result == "unknown"

    def test_returns_version_string_from_file(self):
        import linux_voice_assistant.util as util
        util._version_cache = None

        with patch.object(Path, "read_text", return_value="1.2.3\n"):
            result = util.get_version()
        assert result == "1.2.3"

    def test_strips_whitespace_from_version(self):
        import linux_voice_assistant.util as util
        util._version_cache = None

        with patch.object(Path, "read_text", return_value="  2.0.0  \n"):
            result = util.get_version()
        assert result == "2.0.0"

    def test_returns_unknown_for_empty_file(self):
        import linux_voice_assistant.util as util
        util._version_cache = None

        with patch.object(Path, "read_text", return_value="   "):
            result = util.get_version()
        assert result == "unknown"

    def test_caches_result_after_first_call(self):
        import linux_voice_assistant.util as util
        util._version_cache = None

        with patch.object(Path, "read_text", return_value="3.0.0") as mock_read:
            util.get_version()
            util.get_version()
            mock_read.assert_called_once()

    def test_returns_cached_value_on_second_call(self):
        import linux_voice_assistant.util as util
        util._version_cache = None

        with patch.object(Path, "read_text", return_value="4.0.0"):
            first = util.get_version()

        second = util.get_version()
        assert first == second == "4.0.0"

    def test_returns_unknown_on_permission_error(self):
        import linux_voice_assistant.util as util
        util._version_cache = None

        with patch.object(Path, "read_text", side_effect=PermissionError):
            result = util.get_version()
        assert result == "unknown"


# ---------------------------------------------------------------------------
# get_esphome_version()
# ---------------------------------------------------------------------------


class TestGetEsphomeVersion:
    def setup_method(self):
        import linux_voice_assistant.util as util
        util._esphome_version_cache = None

    def test_returns_version_when_package_installed(self):
        import linux_voice_assistant.util as util

        with patch("linux_voice_assistant.util.version", return_value="42.7.0"):
            result = util.get_esphome_version()
        assert result == "42.7.0"

    def test_returns_unknown_when_package_not_installed(self):
        import linux_voice_assistant.util as util

        from importlib.metadata import PackageNotFoundError
        with patch("linux_voice_assistant.util.version", side_effect=PackageNotFoundError):
            result = util.get_esphome_version()
        assert result == "unknown"

    def test_caches_result_after_first_call(self):
        import linux_voice_assistant.util as util

        with patch("linux_voice_assistant.util.version", return_value="1.0.0") as mock_ver:
            util.get_esphome_version()
            util.get_esphome_version()
            mock_ver.assert_called_once()

    def test_returns_cached_value_on_second_call(self):
        import linux_voice_assistant.util as util

        with patch("linux_voice_assistant.util.version", return_value="5.0.0"):
            first = util.get_esphome_version()

        second = util.get_esphome_version()
        assert first == second == "5.0.0"


# ---------------------------------------------------------------------------
# call_all()
# ---------------------------------------------------------------------------


class TestCallAll:
    def test_calls_single_callable(self):
        from linux_voice_assistant.util import call_all
        mock_fn = MagicMock()
        call_all(mock_fn)
        mock_fn.assert_called_once()

    def test_calls_multiple_callables_in_order(self):
        from linux_voice_assistant.util import call_all
        calls = []
        call_all(
            lambda: calls.append(1),
            lambda: calls.append(2),
            lambda: calls.append(3),
        )
        assert calls == [1, 2, 3]

    def test_skips_none_entries(self):
        from linux_voice_assistant.util import call_all
        mock_fn = MagicMock()
        call_all(None, mock_fn, None)
        mock_fn.assert_called_once()

    def test_all_none_does_nothing(self):
        from linux_voice_assistant.util import call_all
        call_all(None, None, None)

    def test_empty_args_does_nothing(self):
        from linux_voice_assistant.util import call_all
        call_all()

    def test_none_mixed_with_callables_calls_only_non_none(self):
        from linux_voice_assistant.util import call_all
        results = []
        call_all(None, lambda: results.append("a"), None, lambda: results.append("b"))
        assert results == ["a", "b"]


# ---------------------------------------------------------------------------
# get_default_interface() and get_default_ipv4()
# ---------------------------------------------------------------------------


class TestGetDefaultInterface:
    def test_returns_interface_name_from_gateway(self):
        import linux_voice_assistant.util as util
        with patch("linux_voice_assistant.util.netifaces") as mock_netifaces:
            # Set AF_INET before building the dict so the key matches
            mock_netifaces.AF_INET = 2
            mock_netifaces.default_gateway.return_value = {
                2: ("192.168.1.1", "eth0")
            }
            result = util.get_default_interface()
        assert result == "eth0"

    def test_returns_none_when_no_gateway(self):
        import linux_voice_assistant.util as util
        with patch("linux_voice_assistant.util.netifaces") as mock_netifaces:
            mock_netifaces.default_gateway.return_value = {}
            result = util.get_default_interface()
        assert result is None

    def test_returns_none_when_no_ipv4_gateway(self):
        import linux_voice_assistant.util as util
        with patch("linux_voice_assistant.util.netifaces") as mock_netifaces:
            mock_netifaces.AF_INET = 2
            # Only a non-IPv4 gateway present
            mock_netifaces.default_gateway.return_value = {99: ("10.0.0.1", "eth1")}
            result = util.get_default_interface()
        assert result is None


class TestGetDefaultIpv4:
    def test_returns_ip_for_interface(self):
        import linux_voice_assistant.util as util
        with patch("linux_voice_assistant.util.netifaces") as mock_netifaces:
            mock_netifaces.AF_INET = 2
            mock_netifaces.ifaddresses.return_value = {
                2: [{"addr": "192.168.1.50"}]
            }
            result = util.get_default_ipv4("eth0")
        assert result == "192.168.1.50"

    def test_returns_none_for_empty_interface(self):
        import linux_voice_assistant.util as util
        result = util.get_default_ipv4("")
        assert result is None

    def test_returns_none_for_none_interface(self):
        import linux_voice_assistant.util as util
        result = util.get_default_ipv4(None)
        assert result is None

    def test_returns_none_when_no_ipv4_address(self):
        import linux_voice_assistant.util as util
        with patch("linux_voice_assistant.util.netifaces") as mock_netifaces:
            mock_netifaces.AF_INET = 2
            mock_netifaces.ifaddresses.return_value = {}
            result = util.get_default_ipv4("eth0")
        assert result is None