import asyncio
import json
import logging
from typing import TYPE_CHECKING, List, Optional

import paho.mqtt.client as mqtt

from .config import MqttConfig
from .event_bus import EventBus, EventHandler, subscribe
from .models import Preferences, SatelliteState
from .util import slugify_device_id

if TYPE_CHECKING:
    from .models import ServerState

_LOGGER = logging.getLogger(__name__)


class MqttController(EventHandler):
    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        event_bus: EventBus,
        config: MqttConfig,
        app_name: str,
        mac_address: str,
        preferences: Preferences,
    ):
        super().__init__(event_bus)
        self.loop = loop
        self.preferences = preferences

        self._host = config.host
        self._port = config.port
        self._username = config.username
        self._password = config.password

        self._device_name = app_name
        self._device_id = slugify_device_id(app_name)
        self._topic_prefix = f"lva/{self._device_id}"

        self._is_muted = False  # Internal state
        self._connected = False  # Track connection state
        self._reconnect_task: Optional[asyncio.Task] = None
        self._reconnect_delay = 5.0  # Initial reconnect delay in seconds
        self._max_reconnect_delay = 300.0  # Max 5 minutes
        self._should_reconnect = True  # Flag to control reconnection

        self.CONFIGURABLE_STATES: List[str] = [
            SatelliteState.IDLE.value,
            SatelliteState.LISTENING.value,
            SatelliteState.THINKING.value,
            SatelliteState.RESPONDING.value,
            SatelliteState.ERROR.value,
        ]

        self.topics = {
            "mute": {
                "command": f"{self._topic_prefix}/mute/set",
                "state": f"{self._topic_prefix}/mute/state",
            },
            "num_leds": {
                "command": f"{self._topic_prefix}/num_leds/set",
                "state": f"{self._topic_prefix}/num_leds/state",
            },
            "alarm_duration": {
                "command": f"{self._topic_prefix}/alarm_duration/set",
                "state": f"{self._topic_prefix}/alarm_duration/state",
            },
        }

        for state_name in self.CONFIGURABLE_STATES:
            self.topics[state_name] = {
                "effect_command": f"{self._topic_prefix}/{state_name}_effect/set",
                "effect_state": f"{self._topic_prefix}/{state_name}_effect/state",
                "light_command": f"{self._topic_prefix}/{state_name}_light/set",
                "light_state": f"{self._topic_prefix}/{state_name}_light/state",
            }

        self._bootstrap_state_sync = True
        self._bootstrap_ends_at: Optional[float] = None

        self._client = mqtt.Client()
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect

        # Subscribe to events *after* __init__ is complete
        self._subscribe_all_methods()

    def start(self):
        self._should_reconnect = True
        try:
            # Set Last Will and Testament (LWT) as a fallback
            self._client.will_set(
                f"{self._topic_prefix}/availability",
                payload="offline",
                qos=1,
                retain=True,
            )

            if self._username:
                self._client.username_pw_set(self._username, self._password)

            _LOGGER.debug("Connecting to MQTT broker at %s:%s", self._host, self._port)
            self._client.connect(self._host, self._port, 60)
            self._client.loop_start()
        except ConnectionRefusedError as e:
            _LOGGER.error("MQTT broker connection refused at %s:%s", self._host, self._port)
            self._schedule_reconnect()
        except OSError as e:
            _LOGGER.error("MQTT broker network error: %s", e)
            self._schedule_reconnect()
        except Exception as e:
            _LOGGER.error("Failed to connect to MQTT broker: %s", e, exc_info=True)
            self._schedule_reconnect()

    def _publish_offline_blocking(self):
        """Helper to publish offline status and BLOCK until sent."""
        try:
            info = self._client.publish(
                f"{self._topic_prefix}/availability",
                "offline",
                qos=0,
                retain=True,
            )
            info.wait_for_publish(timeout=2.0)
        except Exception:
            _LOGGER.warning("Failed to flush offline message to broker", exc_info=True)

    async def stop(self):
        """Stop the MQTT client and publish offline status."""
        if not self._client:
            return

        _LOGGER.info("Stopping MQTT Controller...")

        # Cancel reconnection attempts
        self._should_reconnect = False
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass

        # 1. Publish Offline Status (Blocking wait)
        if self._connected:
            _LOGGER.info("Publishing availability: offline")
            await self.loop.run_in_executor(None, self._publish_offline_blocking)

        # 2. Stop the loop and disconnect
        await self.loop.run_in_executor(None, self._client.loop_stop)
        await self.loop.run_in_executor(None, self._client.disconnect)
        self._connected = False
        _LOGGER.debug("Disconnected from MQTT broker")

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            _LOGGER.info("Connected to MQTT broker")
            self._connected = True
            client.subscribe(f"{self._topic_prefix}/+/set")
            client.subscribe(f"{self._topic_prefix}/+/state")

            # Increased bootstrap time to ensure retained messages are captured
            self._bootstrap_ends_at = self.loop.time() + 5.0
            self.loop.call_later(5.0, self._end_bootstrap_state_sync)

            self._publish_discovery_configs()
        else:
            _LOGGER.error("Failed to connect to MQTT, return code %d", rc)

    def _on_disconnect(self, client, userdata, rc):
        self._connected = False
        if rc != 0:
            _LOGGER.warning("Unexpected MQTT disconnection (rc=%s). Will attempt reconnection.", rc)
            if self._should_reconnect:
                self._schedule_reconnect()
        else:
            _LOGGER.debug("MQTT client disconnected cleanly")

    def _end_bootstrap_state_sync(self):
        self._bootstrap_state_sync = False
        try:
            self._client.unsubscribe(f"{self._topic_prefix}/+/state")
        except Exception:
            _LOGGER.debug("Failed to unsubscribe from */state topics", exc_info=True)
        _LOGGER.debug("MQTT bootstrap state sync complete; unsubscribed from */state")

    def _on_message(self, client, userdata, msg):
        try:
            topic = msg.topic
            payload = msg.payload.decode()
            _LOGGER.debug("Received MQTT message on topic %s", msg.topic)
            retain = bool(getattr(msg, "retain", False))
            self.loop.call_soon_threadsafe(self._handle_message_on_loop, topic, payload, retain)
        except Exception:
            _LOGGER.exception("Error in _on_message")

    def _handle_message_on_loop(self, topic: str, payload: str, retained: bool = False):
        _LOGGER.debug("Handling message for topic %s on main loop", topic)

        if topic.endswith("/state"):
            if not (self._bootstrap_state_sync and retained):
                _LOGGER.debug("Ignoring MQTT state topic: %s", topic)
                return

        if topic == self.topics["mute"]["command"]:
            self.event_bus.publish("set_mic_mute", {"state": payload.upper() == "ON"})

        elif topic == self.topics["num_leds"]["command"]:
            try:
                num_leds = int(payload)
                self.event_bus.publish("set_num_leds", {"num_leds": num_leds})
                self.publish_num_leds_state(num_leds)
            except ValueError:
                pass

        elif topic == self.topics["alarm_duration"]["command"]:
            try:
                duration = int(payload)
                if duration < 0:
                    raise ValueError
                self.event_bus.publish("set_alarm_duration", {"alarm_duration_seconds": duration})
                self.publish_alarm_duration_state(duration)
            except ValueError:
                _LOGGER.warning("Invalid alarm_duration payload received: %r", payload)

        for state_name in self.CONFIGURABLE_STATES:
            state_topics = self.topics[state_name]
            if topic == state_topics["effect_command"]:
                effect_id = payload.lower().replace(" ", "_")
                self.event_bus.publish(f"set_{state_name}_effect", {"effect": effect_id})

            elif topic == state_topics["light_command"]:
                try:
                    data = json.loads(payload)
                    if "state" in data:
                        if str(data["state"]).upper() == "OFF":
                            self.event_bus.publish(f"set_{state_name}_effect", {"effect": "off"})
                        else:
                            self.event_bus.publish(f"turn_on_{state_name}")

                    if "color" in data or "brightness" in data:
                        self.event_bus.publish(f"set_{state_name}_color", data)
                except json.JSONDecodeError:
                    pass

            elif topic == state_topics["effect_state"]:
                effect_id = payload.lower().replace(" ", "_")
                self.event_bus.publish(
                    f"set_{state_name}_effect",
                    {"effect": effect_id, "retained": retained},
                )

            elif topic == state_topics["light_state"]:
                try:
                    data = json.loads(payload)
                    data["retained"] = retained
                    self.event_bus.publish(f"set_{state_name}_color", data)
                except json.JSONDecodeError:
                    pass

    def _publish_discovery_configs(self):
        availability_topic = f"{self._topic_prefix}/availability"
        device_info = {
            "identifiers": [self._device_id],
            "name": self._device_name,
            "manufacturer": "LVA Project",
        }
        options = [
            "Off",
            "Solid",
            "Slow Pulse",
            "Medium Pulse",
            "Fast Pulse",
            "Slow Blink",
            "Medium Blink",
            "Fast Blink",
            "Spin",
        ]

        mute_cfg = {
            "name": "Mute Microphone",
            "unique_id": f"{self._device_id}_mute",
            "command_topic": self.topics["mute"]["command"],
            "state_topic": self.topics["mute"]["state"],
            "availability_topic": availability_topic,
            "icon": "mdi:microphone-off",
            "device": device_info,
        }
        self._client.publish(
            f"homeassistant/switch/{self._device_id}_mute/config",
            json.dumps(mute_cfg),
            retain=True,
        )

        num_leds_cfg = {
            "name": "Number of LEDs",
            "unique_id": f"{self._device_id}_num_leds",
            "command_topic": self.topics["num_leds"]["command"],
            "state_topic": self.topics["num_leds"]["state"],
            "availability_topic": availability_topic,
            "min": 1,
            "max": 256,
            "step": 1,
            "icon": "mdi:counter",
            "device": device_info,
            "mode": "box",
            "entity_category": "config",
        }
        self._client.publish(
            f"homeassistant/number/{self._device_id}_num_leds/config",
            json.dumps(num_leds_cfg),
            retain=True,
        )

        alarm_duration_cfg = {
            "name": "Alarm Duration",
            "unique_id": f"{self._device_id}_alarm_duration",
            "command_topic": self.topics["alarm_duration"]["command"],
            "state_topic": self.topics["alarm_duration"]["state"],
            "availability_topic": availability_topic,
            "min": 0,
            "max": 3600,
            "step": 5,
            "unit_of_measurement": "s",
            "icon": "mdi:timer",
            "device": device_info,
            "mode": "box",
            "entity_category": "config",
        }
        self._client.publish(
            f"homeassistant/number/{self._device_id}_alarm_duration/config",
            json.dumps(alarm_duration_cfg),
            retain=True,
        )

        for state_name in self.CONFIGURABLE_STATES:
            capital_name = state_name.title()

            select_cfg = {
                "name": f"{capital_name} Effect",
                "unique_id": f"{self._device_id}_{state_name}_effect",
                "command_topic": self.topics[state_name]["effect_command"],
                "state_topic": self.topics[state_name]["effect_state"],
                "availability_topic": availability_topic,
                "options": options,
                "icon": "mdi:palette-swatch-variant",
                "device": device_info,
                "entity_category": "config",
            }
            self._client.publish(
                f"homeassistant/select/{self._device_id}_{state_name}_effect/config",
                json.dumps(select_cfg),
                retain=True,
            )

            light_cfg = {
                "name": f"{capital_name} Color",
                "unique_id": f"{self._device_id}_{state_name}_color",
                "schema": "json",
                "command_topic": self.topics[state_name]["light_command"],
                "state_topic": self.topics[state_name]["light_state"],
                "availability_topic": availability_topic,
                "brightness": True,
                "color_mode": True,
                "supported_color_modes": ["rgb"],
                "device": device_info,
                "entity_category": "config",
            }
            self._client.publish(
                f"homeassistant/light/{self._device_id}_{state_name}_color/config",
                json.dumps(light_cfg),
                retain=True,
            )

        _LOGGER.debug("Published all MQTT discovery configs")
        self._client.publish(availability_topic, "online", retain=True)

        self.publish_mute_state(self._is_muted)
        self.publish_num_leds_state(self.preferences.num_leds)
        self.publish_alarm_duration_state(getattr(self.preferences, "alarm_duration_seconds", 0))

    def publish_mute_state(self, is_muted: bool):
        self._is_muted = is_muted
        self._client.publish(
            self.topics["mute"]["state"],
            "ON" if is_muted else "OFF",
            retain=True,
        )

    def publish_num_leds_state(self, num_leds: int):
        self._client.publish(self.topics["num_leds"]["state"], str(num_leds), retain=True)

    def publish_alarm_duration_state(self, duration_seconds: int):
        self._client.publish(self.topics["alarm_duration"]["state"], str(int(duration_seconds)), retain=True)

    @subscribe
    def publish_state_to_mqtt(self, data: dict):
        state_name = data.get("state_name")
        if state_name in self.topics:
            state_topics = self.topics[state_name]

            effect_name = data.get("effect", "off").replace("_", " ").title()
            self._client.publish(state_topics["effect_state"], effect_name, retain=True)

            light_state = {
                "state": "ON" if data.get("effect") != "off" else "OFF",
                "color_mode": "rgb",
                "brightness": int(data.get("brightness", 0.5) * 255),
                "color": {
                    "r": data.get("color")[0],
                    "g": data.get("color")[1],
                    "b": data.get("color")[2],
                },
            }
            self._client.publish(state_topics["light_state"], json.dumps(light_state), retain=True)

    @subscribe
    def mic_muted(self, data: dict):
        self.publish_mute_state(True)

    @subscribe
    def mic_unmuted(self, data: dict):
        self.publish_mute_state(False)

    def _schedule_reconnect(self):
        """Schedule a reconnection attempt with exponential backoff."""
        if not self._should_reconnect:
            return

        if self._reconnect_task and not self._reconnect_task.done():
            # Already scheduled
            return

        _LOGGER.info("Scheduling MQTT reconnection in %.1f seconds", self._reconnect_delay)
        self._reconnect_task = self.loop.create_task(self._reconnect())

    async def _reconnect(self):
        """Attempt to reconnect to MQTT broker with exponential backoff."""
        await asyncio.sleep(self._reconnect_delay)

        if not self._should_reconnect:
            return

        _LOGGER.info("Attempting to reconnect to MQTT broker...")
        try:
            await self.loop.run_in_executor(None, self._attempt_connection)
            # Reset delay on successful connection
            self._reconnect_delay = 5.0
            _LOGGER.info("Successfully reconnected to MQTT broker")
        except Exception as e:
            _LOGGER.warning("Reconnection attempt failed: %s", e)
            # Exponential backoff with cap
            self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)
            # Schedule next attempt
            if self._should_reconnect:
                self._schedule_reconnect()

    def _attempt_connection(self):
        """Synchronous method to attempt MQTT connection."""
        # Reconnect using paho's reconnect method
        self._client.reconnect()
        # Loop should already be started from initial start() call
