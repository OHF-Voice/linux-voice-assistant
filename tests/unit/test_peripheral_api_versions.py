"""Unit tests for the choice of the peripheral API version (resolve_version)."""

import pytest

from linux_voice_assistant.peripheral_api.versions import DEFAULT_VERSION, resolve_version

SUPPORTED = ("v1", "v2")


class TestResolveVersion:
    @pytest.mark.parametrize("path", ["", "/", "//"])
    def test_empty_path_is_default_version(self, path):
        assert resolve_version(path, SUPPORTED) == DEFAULT_VERSION

    def test_default_version_is_v1(self):
        assert DEFAULT_VERSION == "v1"

    @pytest.mark.parametrize("path,expected", [("/v1", "v1"), ("/v2", "v2"), ("v2", "v2"), ("/v2/", "v2")])
    def test_supported_version(self, path, expected):
        assert resolve_version(path, SUPPORTED) == expected

    def test_query_string_is_ignored(self):
        assert resolve_version("/v2?token=abc", SUPPORTED) == "v2"

    @pytest.mark.parametrize("path", ["/v3", "/v1/extra", "/V1", "/foo"])
    def test_unknown_path_is_refused(self, path):
        assert resolve_version(path, SUPPORTED) is None

    def test_version_that_is_not_supported_is_refused(self):
        assert resolve_version("/v2", ("v1",)) is None

    def test_empty_path_is_default_even_with_other_versions_only(self):
        assert resolve_version("/", ("v2",)) == DEFAULT_VERSION
