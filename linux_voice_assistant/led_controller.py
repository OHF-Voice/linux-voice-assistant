import asyncio
import logging
from typing import Any, Optional, Tuple
import sys

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional hardware imports (board-safe)
# ---------------------------------------------------------------------------
try:
    import board  # type: ignore[import]
except Exception:
    board = None  # type: ignore[assignment]
    _LOGGER.warning(
        "Adafruit 'board' module not available or unsupported on this platform; "
        "DotStar/NeoPixel GPIO/SPI LED backends will be disabled. "
        "XVF3800 USB LED backend is unaffected."
    )

from .config import LedConfig
from .event_bus import EventBus, EventHandler, subscribe
from .models import Preferences

# Default Colors
_OFF = (0, 0, 0)
_BLUE = (0, 0, 255)
_YELLOW = (255, 255, 0)
_GREEN = (0, 255, 0)
_DIM_RED = (50, 0, 0)
_ORANGE = (255, 165, 0)
_PURPLE = (128, 0, 255)


class LedController(EventHandler):
    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        event_bus: EventBus,
        config: LedConfig,
        preferences: Preferences,
    ):
        super().__init__(event_bus)
        self.loop = loop
        self.num_leds = preferences.num_leds  # Get num_leds from preferences
        self.current_task: Optional[asyncio.Future] = None
        self._is_ready = False
        self.leds = None

        # Track mute overlay so reconnect/bootstrap state sync can't override it
        self._mic_is_muted: bool = False

        # Backend mode:
        #   "pixels"  -> DotStar / NeoPixel via Adafruit drivers
        #   "xvf3800" -> XVF3800 USB LED ring backend
        self._backend_mode: str = "pixels"
        self._xvf3800_backend = None

        # Configured LED behavior
        self.configs = {
            "idle":       {"effect": "off",           "color": _PURPLE, "brightness": 0.5},
            "listening":  {"effect": "medium_pulse",  "color": _BLUE,   "brightness": 0.5},
            "thinking":   {"effect": "spin",          "color": _YELLOW, "brightness": 0.8},
            "responding": {"effect": "medium_pulse",  "color": _GREEN,  "brightness": 0.5},
            "error":      {"effect": "fast_blink",    "color": _ORANGE, "brightness": 1.0},
        }

        # Determine whether hardware LEDs should be used at all
        config_enabled = getattr(config, "enabled", True)

        # For XVF3800 we do NOT require the Adafruit 'board' module.
        if config.led_type == "xvf3800" and config.interface == "usb":
            self._enabled = bool(config_enabled)
        else:
            self._enabled = bool(config_enabled) and (board is not None)

        if not config_enabled:
            _LOGGER.info(
                "LEDs disabled in config (led.enabled = false); "
                "LedController will run in no-op mode."
            )
        elif not self._enabled:
            _LOGGER.warning(
                "LED hardware libraries not available on this platform for led_type=%s; "
                "LedController will run in no-op mode.",
                config.led_type,
            )

        # Always subscribe to events so MQTT + state logic remains intact,
        # even when LEDs are effectively disabled.
        self._subscribe_all_methods()

        # If LEDs are not enabled at all, skip hardware init
        if not self._enabled:
            return

        # -------------------------------------------------------------------
        # XVF3800 USB LED backend
        # -------------------------------------------------------------------
        if config.led_type == "xvf3800" and config.interface == "usb":
            try:
                from .xvf3800_led_backend import XVF3800LedBackend  # type: ignore[import]

                self._xvf3800_backend = XVF3800LedBackend()
                self._backend_mode = "xvf3800"
                self._is_ready = True
                # XVF3800 ring has a fixed LED count (typically 12)
                self.num_leds = int(getattr(self._xvf3800_backend, "ring_led_count", 12))
                _LOGGER.info(
                    "LED Controller initialized for XVF3800 USB LED ring (num_leds=%d).",
                    self.num_leds,
                )
                self.run_action("startup_sequence")
            except Exception:
                # Any hardware-related failure leaves us in no-op mode
                self._is_ready = False
                self._enabled = False
                self._xvf3800_backend = None
                _LOGGER.exception(
                    "Failed to initialize XVF3800 LED backend. LEDs will be disabled."
                )
            return

        # -------------------------------------------------------------------
        # Hardware initialization (DotStar / NeoPixel via Adafruit drivers)
        # -------------------------------------------------------------------
        try:
            if config.led_type == "neopixel":
                if config.interface == "spi":
                    import busio
                    import neopixel_spi

                    _LOGGER.debug(
                        "Initializing %d NeoPixel LEDs on hardware SPI", self.num_leds
                    )
                    spi = busio.SPI(board.SCLK, MOSI=board.MOSI)
                    self.leds = neopixel_spi.NeoPixel_SPI(
                        spi, self.num_leds, auto_write=False
                    )
                else:  # GPIO
                    import neopixel

                    _LOGGER.debug(
                        "Initializing %d NeoPixel LEDs on GPIO data=%s",
                        self.num_leds,
                        config.data_pin,
                    )
                    pin_object = getattr(board, f"D{config.data_pin}")
                    self.leds = neopixel.NeoPixel(
                        pin_object, self.num_leds, auto_write=False
                    )
            else:  # dotstar
                import adafruit_dotstar

                if config.interface == "gpio":
                    _LOGGER.debug(
                        "Initializing %d DotStar LEDs on GPIO data=%s, clock=%s",
                        self.num_leds,
                        config.data_pin,
                        config.clock_pin,
                    )
                    data_pin_obj = getattr(board, f"D{config.data_pin}")
                    clock_pin_obj = getattr(board, f"D{config.clock_pin}")
                    self.leds = adafruit_dotstar.DotStar(
                        clock_pin_obj, data_pin_obj, self.num_leds, auto_write=False
                    )
                else:  # SPI
                    _LOGGER.debug(
                        "Initializing %d DotStar LEDs on hardware SPI", self.num_leds
                    )
                    self.leds = adafruit_dotstar.DotStar(
                        board.SCLK, board.MOSI, self.num_leds, auto_write=False
                    )

            self._backend_mode = "pixels"
            self._is_ready = True
            _LOGGER.info(
                "LED Controller initialized for %d %s LEDs.",
                self.num_leds,
                config.led_type,
            )
            self.run_action("startup_sequence")

        except Exception:
            # Any hardware-related failure leaves us in no-op mode
            self._is_ready = False
            self.leds = None
            _LOGGER.exception(
                "Failed to initialize LED controller. LEDs will be disabled."
            )

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def run_action(self, action_method_name: str, *args: Any) -> None:
        """Schedule an LED coroutine on the event loop."""
        if not (self._enabled and self._is_ready):
            return

        if self.current_task and not self.current_task.done():
            self.current_task.cancel()

        coro = getattr(self, action_method_name)(*args)
        self.current_task = asyncio.ensure_future(coro, loop=self.loop)

    def _handle_xvf3800_usb_error(self, operation: str, error: Exception) -> None:
        """
        Handle XVF3800 USB disconnection errors by triggering service restart.
        
        When the XVF3800 USB device becomes unavailable (disconnected or unresponsive),
        we log the error and exit the process. The systemd service (with Restart=always)
        will automatically restart the service, which reinitializes the USB connection
        and fixes LED issues.
        """
        error_msg = str(error)
        # Check if this is a USB-related error
        if any(keyword in error_msg.lower() for keyword in [
            'usb device not found', 
            'xvf3800 usb device not found',
            'usberror',
            'no backend available',
            'device disappeared',
            'runtimeerror'
        ]):
            _LOGGER.error(
                "XVF3800 USB device disconnected or unavailable during %s: %s. "
                "Triggering service restart to reinitialize USB connection...",
                operation, error_msg
            )
            # Exit with code 1 to trigger systemd restart
            sys.exit(1)
        else:
            # For other errors, just log and continue
            _LOGGER.exception("Error during XVF3800 operation '%s'", operation)
        self.current_task = asyncio.run_coroutine_threadsafe(coro, self.loop)

    def _apply_state_effect(self, state_name: str, publish_state: bool = True):
        # Mute has highest precedence over any voice/idle effects.
        if self._mic_is_muted:
            _LOGGER.debug(
                "Mic is muted; overriding requested state '%s' with mute LED",
                state_name,
            )
            self.run_action("solid", _DIM_RED, 1.0)
            return

        config = self.configs.get(state_name, self.configs["idle"])
        _LOGGER.debug(
            "Applying effect for state '%s': %s", state_name, config["effect"]
        )
        self.run_action(config["effect"], config["color"], config["brightness"])

        if publish_state:
            config["state_name"] = state_name
            self.event_bus.publish("publish_state_to_mqtt", config)

    # -----------------------------------------------------------------------
    # XVF3800-specific helpers
    # -----------------------------------------------------------------------

    async def _xvf3800_apply_effect(
        self,
        effect_name: str,
        color: Tuple[int, int, int],
        brightness: float,
    ) -> None:
        """Map generic effect/color/brightness to XVF3800 legacy LED controls."""
        if not (self._enabled and self._is_ready and self._xvf3800_backend):
            return

        # Clamp brightness 0..1 and map to 0..255
        brightness = max(0.0, min(1.0, float(brightness)))
        brightness_255 = int(brightness * 255)

        # Effect mapping to XVF3800 legacy modes
        effect_map = {
            "off": 0,           # LED_EFFECT = off
            "solid": 3,         # single color
            "slow_pulse": 1,    # breath
            "medium_pulse": 1,  # breath
            "fast_pulse": 1,    # breath
            "slow_blink": 1,    # approximate with breath
            "medium_blink": 1,  # approximate with breath
            "fast_blink": 1,    # approximate with breath
            "spin": 2,          # rainbow as a stand-in for "spin"
        }

        speed_map = {
            "off": 0,
            "solid": 0,
            "slow_pulse": 0,
            "medium_pulse": 1,
            "fast_pulse": 2,
            "slow_blink": 0,
            "medium_blink": 1,
            "fast_blink": 2,
            "spin": 1,
        }

        effect_id = effect_map.get(effect_name, 3)
        speed_id = speed_map.get(effect_name, 1)

        r, g, b = color
        try:
            # Apply brightness, color, speed, and effect via USB control transfers.
            self._xvf3800_backend.set_brightness(brightness_255)
            if effect_name != "off":
                self._xvf3800_backend.set_color(r, g, b)
            self._xvf3800_backend.set_speed(speed_id)
            self._xvf3800_backend.set_effect(effect_id)
        except Exception as e:
            self._handle_xvf3800_usb_error("apply_effect", e)
            _LOGGER.exception(
                "Error sending LED effect '%s' to XVF3800 backend", effect_name
            )

        # Keep coroutine alive until cancelled so that a new effect
        # can cancel the previous one consistently.
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            return

    # -----------------------------------------------------------------------
    # LED effect coroutines
    # -----------------------------------------------------------------------


    # -----------------------------------------------------------------------
    # XVF3800 per-LED helpers (newer firmware)
    # -----------------------------------------------------------------------

    def _xvf3800_has_per_led(self) -> bool:
        return bool(getattr(self._xvf3800_backend, "supports_per_led", False))

    def _xvf3800_ring_count(self) -> int:
        return int(getattr(self._xvf3800_backend, "ring_led_count", 12))

    def _xvf3800_rgb_clamp(self, color):
        r, g, b = color
        return (
            max(0, min(255, int(r))),
            max(0, min(255, int(g))),
            max(0, min(255, int(b))),
        )

    def _xvf3800_brightness_255(self, brightness: float) -> int:
        b = max(0.0, min(1.0, float(brightness)))
        return int(b * 255)

    def _xvf3800_apply_ring_solid(self, color, brightness: float) -> None:
        """Per-LED solid ring.

        NOTE: Some XVF3800 firmwares do not apply LED_BRIGHTNESS to LED_RING_COLOR output.
        To keep behavior consistent, we scale the RGB values by brightness and write the ring
        colors directly.
        """
        if not self._xvf3800_backend:
            return
        r, g, b = self._xvf3800_rgb_clamp(color)
        brightness = max(0.0, min(1.0, float(brightness)))

        # Ensure firmware effects don't override per-LED output.
        try:
            self._xvf3800_backend.set_effect(0)
        except Exception:
            pass

        rs = int(r * brightness)
        gs = int(g * brightness)
        bs = int(b * brightness)
        try:
            self._xvf3800_backend.set_ring_solid(rs, gs, bs)
        except Exception as e:
            self._handle_xvf3800_usb_error("set_ring_solid", e)

    def _xvf3800_apply_ring_clear(self) -> None:
        if not self._xvf3800_backend:
            return
        try:
            self._xvf3800_backend.set_effect(0)
        except Exception:
            pass
        try:
            self._xvf3800_backend.clear_ring()
        except Exception as e:
            self._handle_xvf3800_usb_error("clear_ring", e)

    async def startup_sequence(self, color=None, brightness=None):
        # Use a simple green blink on startup for all backends.
        await self.blink(_GREEN, 1.0)


    async def off(self, color, brightness):
        if not (self._enabled and self._is_ready):
            return

        if self._backend_mode == "xvf3800":
            # Prefer per-LED ring control when supported by firmware.
            if self._xvf3800_has_per_led():
                try:
                    self._xvf3800_apply_ring_clear()
                except Exception:
                    _LOGGER.exception("Error sending per-LED OFF to XVF3800")
                return

            await self._xvf3800_apply_effect("off", _OFF, 0.0)
            return

        self.leds.fill(_OFF)
        self.leds.show()


    async def solid(self, color: Tuple[int, int, int], brightness: float):
        if not (self._enabled and self._is_ready):
            return

        if self._backend_mode == "xvf3800":
            if self._xvf3800_has_per_led():
                try:
                    self._xvf3800_apply_ring_solid(color, brightness)
                except Exception:
                    _LOGGER.exception("Error sending per-LED SOLID to XVF3800")
                return

            await self._xvf3800_apply_effect("solid", color, brightness)
            return

        r, g, b = color
        self.leds.fill((int(r * brightness), int(g * brightness), int(b * brightness)))
        self.leds.show()

    async def blink(self, color, brightness=1.0):
        await self.medium_blink(color, brightness)


    async def _base_pulse(
        self,
        effect_name: str,
        color: Tuple[int, int, int],
        brightness: float,
        speed: float,
    ):
        if not (self._enabled and self._is_ready):
            return

        if self._backend_mode == "xvf3800":
            if self._xvf3800_has_per_led():
                try:
                    r, g, b = self._xvf3800_rgb_clamp(color)
                    # Pulse by scaling the per-LED RGB values (some firmwares don't apply LED_BRIGHTNESS to LED_RING_COLOR).
                    self._xvf3800_backend.set_effect(0)
                    brightness = max(0.0, min(1.0, float(brightness)))
                    while True:
                        for i in range(0, 101, 10):
                            s = (i / 100.0) * brightness
                            try:
                                self._xvf3800_backend.set_ring_solid(int(r * s), int(g * s), int(b * s))
                            except Exception as e:
                                self._handle_xvf3800_usb_error("set_ring_solid", e)
                            await asyncio.sleep(speed)
                        for i in range(100, -1, -10):
                            s = (i / 100.0) * brightness
                            try:
                                self._xvf3800_backend.set_ring_solid(int(r * s), int(g * s), int(b * s))
                            except Exception as e:
                                self._handle_xvf3800_usb_error("set_ring_solid", e)
                            await asyncio.sleep(speed)
                    return
                except Exception:
                    _LOGGER.exception("Error sending per-LED PULSE to XVF3800")
                    return

            await self._xvf3800_apply_effect(effect_name, color, brightness)
            return

        try:
            r, g, b = color
            while True:
                for i in range(0, 101, 10):
                    mod = i / 100.0 * brightness
                    self.leds.fill((int(r * mod), int(g * mod), int(b * mod)))
                    self.leds.show()
                    await asyncio.sleep(speed)
                for i in range(100, -1, -10):
                    mod = i / 100.0 * brightness
                    self.leds.fill((int(r * mod), int(g * mod), int(b * mod)))
                    self.leds.show()
                    await asyncio.sleep(speed)
        except asyncio.CancelledError:
            if self._enabled and self._is_ready and self.leds is not None:
                self.leds.fill(_OFF)
                self.leds.show()

    async def slow_pulse(self, color, brightness):
        await self._base_pulse("slow_pulse", color, brightness, 0.05)

    async def medium_pulse(self, color, brightness):
        await self._base_pulse("medium_pulse", color, brightness, 0.02)

    async def fast_pulse(self, color, brightness):
        await self._base_pulse("fast_pulse", color, brightness, 0.008)


    async def _base_blink(
        self,
        effect_name: str,
        color: Tuple[int, int, int],
        brightness: float,
        speed: float,
    ):
        if not (self._enabled and self._is_ready):
            return

        if self._backend_mode == "xvf3800":
            if self._xvf3800_has_per_led():
                try:
                    r, g, b = self._xvf3800_rgb_clamp(color)
                    self._xvf3800_backend.set_effect(0)
                    brightness = max(0.0, min(1.0, float(brightness)))
                    while True:
                        # On
                        try:
                            self._xvf3800_backend.set_ring_solid(int(r * brightness), int(g * brightness), int(b * brightness))
                        except Exception as e:
                            self._handle_xvf3800_usb_error("set_ring_solid", e)
                        await asyncio.sleep(speed)
                        # Off
                        try:
                            self._xvf3800_backend.clear_ring()
                        except Exception as e:
                            self._handle_xvf3800_usb_error("clear_ring", e)
                        await asyncio.sleep(speed)
                except asyncio.CancelledError:
                    try:
                        self._xvf3800_apply_ring_clear()
                    except Exception:
                        pass
                    return
                except Exception:
                    _LOGGER.exception("Error sending per-LED BLINK to XVF3800")
                    return

            await self._xvf3800_apply_effect(effect_name, color, brightness)
            return

        try:
            r, g, b = color
            bright_color = (int(r * brightness), int(g * brightness), int(b * brightness))
            while True:
                self.leds.fill(bright_color)
                self.leds.show()
                await asyncio.sleep(speed)
                self.leds.fill(_OFF)
                self.leds.show()
                await asyncio.sleep(speed)
        except asyncio.CancelledError:
            if self._enabled and self._is_ready and self.leds is not None:
                self.leds.fill(_OFF)
                self.leds.show()

    async def slow_blink(self, color, brightness):
        await self._base_blink("slow_blink", color, brightness, 1.0)

    async def medium_blink(self, color, brightness):
        await self._base_blink("medium_blink", color, brightness, 0.5)

    async def fast_blink(self, color, brightness):
        await self._base_blink("fast_blink", color, brightness, 0.1)


    async def spin(
        self, color: Tuple[int, int, int], brightness: float, speed: float = 0.1
    ):
        if not (self._enabled and self._is_ready):
            return

        if self._backend_mode == "xvf3800":
            if self._xvf3800_has_per_led():
                try:
                    r, g, b = self._xvf3800_rgb_clamp(color)
                    ring_n = self._xvf3800_ring_count()
                    self._xvf3800_backend.set_effect(0)
                    self._xvf3800_backend.set_brightness(self._xvf3800_brightness_255(brightness))

                    i = 0
                    while True:
                        colors = [(0, 0, 0)] * ring_n
                        colors[i % ring_n] = (r, g, b)
                        try:
                            self._xvf3800_backend.set_ring_rgb(colors)
                        except Exception as e:
                            self._handle_xvf3800_usb_error("set_ring_rgb", e)
                        i += 1
                        await asyncio.sleep(speed)
                except asyncio.CancelledError:
                    try:
                        self._xvf3800_apply_ring_clear()
                    except Exception:
                        pass
                    return
                except Exception:
                    _LOGGER.exception("Error sending per-LED SPIN to XVF3800")
                    return

            await self._xvf3800_apply_effect("spin", color, brightness)
            return

        try:
            i = 0
            bright_color = (
                int(color[0] * brightness),
                int(color[1] * brightness),
                int(color[2] * brightness),
            )
            while True:
                self.leds.fill(_OFF)
                self.leds[i % self.num_leds] = bright_color
                self.leds.show()
                i += 1
                await asyncio.sleep(speed)
        except asyncio.CancelledError:
            if self._enabled and self._is_ready and self.leds is not None:
                self.leds.fill(_OFF)
                self.leds.show()

    @subscribe
    def voice_idle(self, data: dict):
        self._apply_state_effect("idle")

    @subscribe
    def voice_listen(self, data: dict):
        self._apply_state_effect("listening")

    @subscribe
    def voice_thinking(self, data: dict):
        self._apply_state_effect("thinking")

    @subscribe
    def voice_responding(self, data: dict):
        self._apply_state_effect("responding")

    @subscribe
    def voice_error(self, data: dict):
        self._apply_state_effect("error")

    @subscribe
    def mic_muted(self, data: dict):
        # Mute overlay has precedence over any other state.
        self._mic_is_muted = True
        # When muted, show a solid dim red on all backends.
        self.run_action("solid", _DIM_RED, 1.0)

    @subscribe
    def mic_unmuted(self, data: dict):
        # Clear overlay and return to idle (or other future state logic).
        self._mic_is_muted = False
        self._apply_state_effect("idle")

    # -----------------------------------------------------------------------
    # MQTT Config Subscriptions
    # -----------------------------------------------------------------------

    def _update_config(self, state_name: str, data: dict, apply: bool):
        config = self.configs[state_name]
        changed = False
        is_retained = data.get("retained", False)

        new_effect = data.get("effect")
        if new_effect is not None and config["effect"] != new_effect:
            config["effect"] = new_effect
            changed = True

        new_color_data = data.get("color")
        if new_color_data is not None:
            new_color_tuple = (
                new_color_data.get("r"),
                new_color_data.get("g"),
                new_color_data.get("b"),
            )
            if config["color"] != new_color_tuple:
                config["color"] = new_color_tuple
                changed = True

        new_brightness = data.get("brightness")
        if new_brightness is not None:
            new_brightness_float = new_brightness / 255.0
            if abs(config["brightness"] - new_brightness_float) > 0.001:
                config["brightness"] = new_brightness_float
                changed = True

        if is_retained:
            if changed and state_name == "idle":
                # Re-apply idle but don't republish back to MQTT.
                # IMPORTANT: Don't override mute overlay during reconnect/bootstrap.
                if not self._mic_is_muted:
                    self._apply_state_effect("idle", publish_state=False)
                else:
                    _LOGGER.debug(
                        "Retained idle config received, but mic is muted; skipping idle re-apply"
                    )
            return

        if changed:
            if apply:
                self._apply_state_effect(state_name)
            else:
                config["state_name"] = state_name
                self.event_bus.publish("publish_state_to_mqtt", config)

    @subscribe
    def set_idle_effect(self, data: dict):
        self._update_config("idle", data, True)

    @subscribe
    def set_idle_color(self, data: dict):
        self._update_config(
            "idle",
            data,
            self.configs["idle"]["effect"] != "off",
        )

    @subscribe
    def set_listening_effect(self, data: dict):
        self._update_config("listening", data, False)

    @subscribe
    def set_listening_color(self, data: dict):
        self._update_config("listening", data, False)

    @subscribe
    def set_thinking_effect(self, data: dict):
        self._update_config("thinking", data, False)

    @subscribe
    def set_thinking_color(self, data: dict):
        self._update_config("thinking", data, False)

    @subscribe
    def set_responding_effect(self, data: dict):
        self._update_config("responding", data, False)

    @subscribe
    def set_responding_color(self, data: dict):
        self._update_config("responding", data, False)

    @subscribe
    def set_error_effect(self, data: dict):
        self._update_config("error", data, False)

    @subscribe
    def set_error_color(self, data: dict):
        self._update_config("error", data, False)

    @subscribe
    def set_num_leds(self, data: dict):
        num_leds = data.get("num_leds")
        if (num_leds is not None) and (self.num_leds != num_leds):
            _LOGGER.info(
                "Number of LEDs changed to %d. Please restart for change to take effect.",
                num_leds,
            )
