"""Unit tests for the Hardware Mute switch of the satellite (register_pending_hardware_mute)."""

import logging
from unittest.mock import MagicMock, patch

from tests.unit.conftest import make_satellite


class TestHardwareMuteSwitch:
    @staticmethod
    def reconnect(sat):
        from linux_voice_assistant.satellite import VoiceSatelliteProtocol

        with (
            patch("linux_voice_assistant.satellite.WakeWord1SensitivityNumberEntity", MagicMock()),
            patch("linux_voice_assistant.satellite.WakeWord2SensitivityNumberEntity", MagicMock()),
            patch("linux_voice_assistant.satellite.StopWordSensitivityNumberEntity", MagicMock()),
        ):
            return VoiceSatelliteProtocol(sat.state)

    def test_there_is_no_switch_until_a_peripheral_has_reported(self, tmp_path):
        sat = make_satellite(tmp_path)
        assert sat.state.hardware_mute_switch_entity is None
        assert not [e for e in sat.state.entities if e.__class__.__name__ == "HardwareMuteSwitchEntity"]

    def test_register_does_nothing_while_nothing_is_pending(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat.register_pending_hardware_mute()
        assert sat.state.hardware_mute_switch_entity is None

    def test_the_switch_is_created_once_pending(self, tmp_path, caplog):
        sat = make_satellite(tmp_path)
        sat.state.pending_hardware_mute = True

        with caplog.at_level(logging.INFO):
            sat.register_pending_hardware_mute()
            sat.register_pending_hardware_mute()

        entity = sat.state.hardware_mute_switch_entity
        assert (entity.name, entity.object_id) == ("Hardware Mute", "hardware_mute")
        assert [e for e in sat.state.entities if e is entity] == [entity]
        assert caplog.text.count("Hardware mute switch entity materialised") == 1

    def test_it_is_appended_so_the_keys_of_the_others_do_not_change(self, tmp_path):
        sat = make_satellite(tmp_path)
        keys_before = {id(e): e.key for e in sat.state.entities}
        sat.state.pending_hardware_mute = True

        sat.register_pending_hardware_mute()

        assert {id(e): e.key for e in sat.state.entities if id(e) in keys_before} == keys_before
        assert sat.state.entities[-1] is sat.state.hardware_mute_switch_entity
        assert sat.state.hardware_mute_switch_entity.key == len(sat.state.entities) - 1

    def test_a_new_connection_creates_it_when_a_peripheral_reported_before(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat.state.pending_hardware_mute = True

        again = self.reconnect(sat)

        assert again.state.hardware_mute_switch_entity is not None
        assert again.state.hardware_mute_switch_entity.server is again

    def test_a_new_connection_keeps_the_entity_and_its_key(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat.state.pending_hardware_mute = True
        sat.register_pending_hardware_mute()
        entity = sat.state.hardware_mute_switch_entity
        key = entity.key

        again = self.reconnect(sat)

        assert again.state.hardware_mute_switch_entity is entity
        assert entity.key == key
        assert entity.server is again
        assert [e for e in again.state.entities if e is entity] == [entity]

    def test_a_new_connection_puts_a_removed_entity_back(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat.state.pending_hardware_mute = True
        sat.register_pending_hardware_mute()
        entity = sat.state.hardware_mute_switch_entity
        sat.state.entities.remove(entity)

        again = self.reconnect(sat)

        assert entity in again.state.entities

    def test_the_switch_shows_the_reported_state(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat.state.pending_hardware_mute = True
        sat.register_pending_hardware_mute()
        entity = sat.state.hardware_mute_switch_entity
        assert entity.state_message().state is False
        sat.state.hardware_muted = True
        assert entity.state_message().state is True

    def test_switching_asks_the_peripheral_api(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat.state.peripheral_api = MagicMock()
        sat._request_hardware_mute(True)  # pylint: disable=protected-access
        sat.state.peripheral_api.request_hardware_mute_sync.assert_called_once_with(True)

    def test_switching_without_peripheral_api_does_nothing(self, tmp_path):
        sat = make_satellite(tmp_path)
        sat.state.peripheral_api = None
        sat._request_hardware_mute(False)  # pylint: disable=protected-access
