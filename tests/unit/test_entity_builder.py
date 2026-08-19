"""Unit tests for the generic entity builder (ProxiedEntity + register_entity)."""

import json
from unittest.mock import MagicMock, patch

import pytest
from aioesphomeapi.api_pb2 import (  # type: ignore[attr-defined]
    BinarySensorStateResponse,
    ListEntitiesBinarySensorResponse,
    ListEntitiesRequest,
    ListEntitiesSensorResponse,
    ListEntitiesTextSensorResponse,
    SensorStateResponse,
    SubscribeHomeAssistantStatesRequest,
    TextSensorStateResponse,
)

from tests.unit.conftest import make_satellite

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SENSOR_SPEC = {
    "component": "sensor",
    "object_id": "living_room_temp",
    "name": "Living Room Temperature",
    "device_class": "temperature",
    "unit_of_measurement": "°C",
    "state_class": 1,
    "accuracy_decimals": 1,
    "icon": "mdi:thermometer",
}


def make_api(state=None):
    """PeripheralAPIServer without a running WebSocket server."""
    from linux_voice_assistant.peripheral_api import PeripheralAPIServer

    api = PeripheralAPIServer()
    if state is not None:
        api.set_state(state)
    return api


def make_proxied(component="sensor", spec=None, key=0):
    from linux_voice_assistant.entity import ProxiedEntity

    if spec is None:
        spec = dict(SENSOR_SPEC)
    return ProxiedEntity(server=MagicMock(), key=key, component=component, spec=spec)


def register(api, satellite, **data):
    api._register_entity(data, satellite)


def only(messages):
    """Return the single message yielded by handle_message."""
    items = list(messages)
    assert len(items) == 1
    return items[0]


# ---------------------------------------------------------------------------
# is_reporting_component (schema-derived component gate)
# ---------------------------------------------------------------------------


class TestIsReportingComponent:
    def test_reporting_components_accepted(self):
        from linux_voice_assistant.entity import is_reporting_component

        for component in ("sensor", "binary_sensor", "text_sensor", "number", "select"):
            assert is_reporting_component(component) is True, component

    def test_non_reporting_components_rejected(self):
        from linux_voice_assistant.entity import is_reporting_component

        for component in ("switch", "cover", "fan", "light", "media_player", "event"):
            assert is_reporting_component(component) is False, component

    def test_unknown_components_rejected(self):
        from linux_voice_assistant.entity import is_reporting_component

        for component in ("", "nonsense", "os_path", "sensor2"):
            assert is_reporting_component(component) is False, component


# ---------------------------------------------------------------------------
# ProxiedEntity
# ---------------------------------------------------------------------------


class TestProxiedEntity:
    def test_list_entities_response_passes_descriptor_through(self):
        entity = make_proxied(key=7)

        responses = list(entity.handle_message(ListEntitiesRequest()))
        assert len(responses) == 1
        msg = responses[0]
        assert isinstance(msg, ListEntitiesSensorResponse)
        assert msg.key == 7
        assert msg.object_id == "living_room_temp"
        assert msg.name == "Living Room Temperature"
        assert msg.device_class == "temperature"
        assert msg.unit_of_measurement == "°C"
        assert msg.state_class == 1
        assert msg.accuracy_decimals == 1
        assert msg.icon == "mdi:thermometer"

    def test_unknown_descriptor_keys_dropped(self):
        spec = dict(SENSOR_SPEC, component="sensor", not_a_field="boom")
        entity = make_proxied(spec=spec)

        msg = only(entity.handle_message(ListEntitiesRequest()))
        assert isinstance(msg, ListEntitiesSensorResponse)

    def test_reserved_fields_stripped(self):
        """A peripheral cannot reassign key or move the entity to another device."""
        spec = dict(SENSOR_SPEC, key=999, device_id=5)
        entity = make_proxied(spec=spec, key=3)

        msg = only(entity.handle_message(ListEntitiesRequest()))
        assert msg.key == 3
        assert msg.device_id == 0

    def test_missing_state_until_first_reading(self):
        entity = make_proxied()

        msg = only(entity.handle_message(SubscribeHomeAssistantStatesRequest()))
        assert isinstance(msg, SensorStateResponse)
        assert msg.missing_state is True

        entity.update_state(21.4)
        msg = only(entity.handle_message(SubscribeHomeAssistantStatesRequest()))
        assert msg.missing_state is False
        assert msg.state == pytest.approx(21.4)

    def test_binary_sensor_state(self):
        entity = make_proxied("binary_sensor", {"object_id": "presence", "name": "Presence", "device_class": "occupancy"})

        msg = only(entity.handle_message(ListEntitiesRequest()))
        assert isinstance(msg, ListEntitiesBinarySensorResponse)

        entity.update_state(True)
        state_msg = only(entity.handle_message(SubscribeHomeAssistantStatesRequest()))
        assert isinstance(state_msg, BinarySensorStateResponse)
        assert state_msg.state is True
        assert state_msg.missing_state is False

    def test_text_sensor_state(self):
        entity = make_proxied("text_sensor", {"object_id": "last_seen", "name": "Last Seen"})

        msg = only(entity.handle_message(ListEntitiesRequest()))
        assert isinstance(msg, ListEntitiesTextSensorResponse)

        entity.update_state("kitchen")
        state_msg = only(entity.handle_message(SubscribeHomeAssistantStatesRequest()))
        assert isinstance(state_msg, TextSensorStateResponse)
        assert state_msg.state == "kitchen"

    def test_update_state_wrong_type_raises_and_keeps_old_state(self):
        entity = make_proxied()
        entity.update_state(20.0)

        with pytest.raises(TypeError):
            entity.update_state("not a number")

        msg = only(entity.handle_message(SubscribeHomeAssistantStatesRequest()))
        assert msg.state == pytest.approx(20.0)

    def test_bad_descriptor_type_raises_at_construction(self):
        with pytest.raises(TypeError):
            make_proxied(spec={"object_id": "x", "name": 123})


# ---------------------------------------------------------------------------
# register_entity via PeripheralAPIServer
# ---------------------------------------------------------------------------


class TestRegisterEntity:
    def test_register_without_satellite_stays_pending(self, tmp_path):
        sat = make_satellite(tmp_path)
        state = sat.state
        state.satellite = None
        api = make_api(state)

        register(api, None, **SENSOR_SPEC)

        assert len(state.pending_entities) == 1
        assert state.pending_entities[0].component == "sensor"
        assert state.pending_entities[0].object_id == "living_room_temp"
        assert "living_room_temp" not in state.proxied_entities

    def test_register_with_satellite_materialises(self, tmp_path):
        sat = make_satellite(tmp_path)
        api = make_api(sat.state)

        register(api, sat, **SENSOR_SPEC)

        entity = sat.state.proxied_entities.get("living_room_temp")
        assert entity is not None
        assert entity in sat.state.entities
        assert entity.server is sat

    def test_register_is_idempotent(self, tmp_path):
        sat = make_satellite(tmp_path)
        api = make_api(sat.state)

        register(api, sat, **SENSOR_SPEC)
        first = sat.state.proxied_entities["living_room_temp"]
        register(api, sat, **SENSOR_SPEC)

        assert sat.state.proxied_entities["living_room_temp"] is first
        assert len(sat.state.pending_entities) == 1
        assert sat.state.entities.count(first) == 1

    def test_entities_get_distinct_keys(self, tmp_path):
        sat = make_satellite(tmp_path)
        api = make_api(sat.state)

        register(api, sat, **SENSOR_SPEC)
        register(api, sat, component="binary_sensor", object_id="presence", name="Presence")

        keys = [entity.key for entity in sat.state.proxied_entities.values()]
        assert len(keys) == len(set(keys)) == 2
        all_keys = [getattr(entity, "key", None) for entity in sat.state.entities]
        assert len([k for k in all_keys if k is not None]) == len(set(k for k in all_keys if k is not None))

    def test_bad_component_rejected(self, tmp_path):
        sat = make_satellite(tmp_path)
        api = make_api(sat.state)

        for component in ("switch", "light", "cover", "nonsense", "Sensor", "os.path", "sensor; rm -rf", ""):
            register(api, sat, component=component, object_id="x")

        assert not sat.state.pending_entities
        assert not sat.state.proxied_entities

    def test_bad_object_id_rejected(self, tmp_path):
        sat = make_satellite(tmp_path)
        api = make_api(sat.state)

        for object_id in ("", "Bad-ID", "UPPER", "has space", "x" * 65, "emoji☃"):
            register(api, sat, component="sensor", object_id=object_id)

        assert not sat.state.pending_entities

    def test_collision_with_pending_light_rejected(self, tmp_path):
        from linux_voice_assistant.models import LightRegistration

        sat = make_satellite(tmp_path)
        sat.state.pending_lights.append(LightRegistration(name="LEDs", object_id="leds"))
        api = make_api(sat.state)

        register(api, sat, component="sensor", object_id="leds")

        assert not sat.state.pending_entities

    def test_collision_with_existing_entity_rejected(self, tmp_path):
        sat = make_satellite(tmp_path)
        api = make_api(sat.state)
        register(api, sat, **SENSOR_SPEC)
        # Simulate a second peripheral trying to shadow the same object_id
        # after the registration record was somehow lost.
        sat.state.pending_entities.clear()

        register(api, sat, component="binary_sensor", object_id="living_room_temp")

        assert not sat.state.pending_entities

    def test_entity_cap_enforced(self, tmp_path):
        sat = make_satellite(tmp_path)
        api = make_api(sat.state)
        api.MAX_PROXIED_ENTITIES = 2

        register(api, sat, component="sensor", object_id="one")
        register(api, sat, component="sensor", object_id="two")
        register(api, sat, component="sensor", object_id="three")

        assert len(sat.state.pending_entities) == 2
        assert "three" not in sat.state.proxied_entities

    def test_invalid_descriptor_field_rejected(self, tmp_path):
        sat = make_satellite(tmp_path)
        api = make_api(sat.state)

        register(api, sat, component="sensor", object_id="x", name=123)

        assert not sat.state.pending_entities
        assert not sat.state.proxied_entities

    def test_no_state_is_noop(self):
        api = make_api()
        register(api, None, **SENSOR_SPEC)  # must not raise


# ---------------------------------------------------------------------------
# update_entity via PeripheralAPIServer
# ---------------------------------------------------------------------------


class TestUpdateEntity:
    def test_update_routes_to_entity_and_pushes_to_ha(self, tmp_path):
        sat = make_satellite(tmp_path)
        api = make_api(sat.state)
        register(api, sat, **SENSOR_SPEC)
        sat._writelines.reset_mock()

        api._update_entity({"object_id": "living_room_temp", "state": 21.4}, sat)

        entity = sat.state.proxied_entities["living_room_temp"]
        msg = only(entity.handle_message(SubscribeHomeAssistantStatesRequest()))
        assert msg.state == pytest.approx(21.4)
        assert sat._writelines.called

    def test_update_unknown_object_id_ignored(self, tmp_path):
        sat = make_satellite(tmp_path)
        api = make_api(sat.state)

        api._update_entity({"object_id": "ghost", "state": 1.0}, sat)  # must not raise

    def test_update_wrong_type_ignored(self, tmp_path):
        sat = make_satellite(tmp_path)
        api = make_api(sat.state)
        register(api, sat, **SENSOR_SPEC)

        api._update_entity({"object_id": "living_room_temp", "state": "abc"}, sat)

        entity = sat.state.proxied_entities["living_room_temp"]
        msg = only(entity.handle_message(SubscribeHomeAssistantStatesRequest()))
        assert msg.missing_state is True


# ---------------------------------------------------------------------------
# Satellite lifecycle (materialisation + HA reconnect)
# ---------------------------------------------------------------------------


class TestSatelliteLifecycle:
    def test_pending_entity_materialised_on_construction(self, tmp_path):
        from linux_voice_assistant.models import EntityRegistration

        sat = make_satellite(
            tmp_path,
            state_overrides={"pending_entities": [EntityRegistration(component="sensor", object_id="living_room_temp", spec=dict(SENSOR_SPEC))]},
        )

        assert "living_room_temp" in sat.state.proxied_entities
        assert sat.state.proxied_entities["living_room_temp"] in sat.state.entities

    def test_reconnect_reattaches_existing_entity(self, tmp_path):
        from linux_voice_assistant.satellite import VoiceSatelliteProtocol

        sat1 = make_satellite(tmp_path)
        api = make_api(sat1.state)
        register(api, sat1, **SENSOR_SPEC)
        entity = sat1.state.proxied_entities["living_room_temp"]
        entity.update_state(19.5)

        # HA reconnect: a fresh protocol instance is constructed over the same state.
        with (
            patch("linux_voice_assistant.satellite.WakeWord1SensitivityNumberEntity", MagicMock()),
            patch("linux_voice_assistant.satellite.WakeWord2SensitivityNumberEntity", MagicMock()),
            patch("linux_voice_assistant.satellite.StopWordSensitivityNumberEntity", MagicMock()),
        ):
            sat2 = VoiceSatelliteProtocol(sat1.state)

        assert sat1.state.proxied_entities["living_room_temp"] is entity
        assert entity.server is sat2
        assert sat1.state.entities.count(entity) == 1
        # State survives the reconnect
        msg = only(entity.handle_message(SubscribeHomeAssistantStatesRequest()))
        assert msg.state == pytest.approx(19.5)

    def test_bad_pending_spec_skipped_without_crashing_satellite(self, tmp_path):
        from linux_voice_assistant.models import EntityRegistration

        sat = make_satellite(
            tmp_path,
            state_overrides={"pending_entities": [EntityRegistration(component="sensor", object_id="bad", spec={"object_id": "bad", "name": 123})]},
        )

        assert "bad" not in sat.state.proxied_entities


# ---------------------------------------------------------------------------
# WebSocket dispatch integration
# ---------------------------------------------------------------------------


class TestDispatch:
    async def test_register_and_update_via_dispatch(self, tmp_path):
        sat = make_satellite(tmp_path)
        api = make_api(sat.state)

        await api._dispatch_command(json.dumps({"command": "register_entity", "data": dict(SENSOR_SPEC)}))
        assert "living_room_temp" in sat.state.proxied_entities

        await api._dispatch_command(json.dumps({"command": "update_entity", "data": {"object_id": "living_room_temp", "state": 22.0}}))
        entity = sat.state.proxied_entities["living_room_temp"]
        msg = only(entity.handle_message(SubscribeHomeAssistantStatesRequest()))
        assert msg.state == pytest.approx(22.0)

    async def test_register_entity_without_data_ignored(self, tmp_path):
        sat = make_satellite(tmp_path)
        api = make_api(sat.state)

        await api._dispatch_command(json.dumps({"command": "register_entity"}))  # must not raise

        assert not sat.state.pending_entities
