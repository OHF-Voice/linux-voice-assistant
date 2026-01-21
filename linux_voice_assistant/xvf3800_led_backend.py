"""XVF3800 USB LED ring backend.

This backend supports two generations of LED control:

1) Legacy/global LED control (older firmware):
   - LED_EFFECT, LED_BRIGHTNESS, LED_SPEED, LED_COLOR

2) Per-LED ring control (newer firmware):
   - LED_RING_COLOR: 12x uint32 values, one per LED (0xRRGGBB)

We intentionally keep the implementation minimal and self-contained so it can be
used by LVA without shelling out to xvf_host.py.
"""

from __future__ import annotations

import logging
import struct
import time
from typing import List, Optional, Sequence, Tuple

import usb.core  # type: ignore[import]
import usb.util  # type: ignore[import]

_LOGGER = logging.getLogger(__name__)

CONTROL_SUCCESS = 0
SERVICER_COMMAND_RETRY = 64

# name -> (resid, cmdid, count, access, type)
PARAMETERS = {
    # ---------------------------------------------------------------------
    # APPLICATION_SERVICER_RESID (core info)
    # ---------------------------------------------------------------------
    "VERSION": (48, 0, 3, "ro", "uint8"),

    # Firmware / device control
    "REBOOT":             (48, 7, 1, "wo", "uint8"),
    "SAVE_CONFIGURATION": (48, 9, 1, "wo", "uint8"),

    # Audio manager output routing (category, source) per channel
    "AUDIO_MGR_OP_L":     (35, 15, 2, "rw", "uint8"),
    "AUDIO_MGR_OP_R":     (35, 19, 2, "rw", "uint8"),

    # ---------------------------------------------------------------------
    # GPO_SERVICER_RESID (LED controls)
    # ---------------------------------------------------------------------
    # Legacy/global controls
    "LED_EFFECT":     (20, 12, 1, "rw", "uint8"),
    "LED_BRIGHTNESS": (20, 13, 1, "rw", "uint8"),
    "LED_GAMMIFY":    (20, 14, 1, "rw", "uint8"),
    "LED_SPEED":      (20, 15, 1, "rw", "uint8"),
    "LED_COLOR":      (20, 16, 1, "rw", "uint32"),

    # Newer firmware: per-LED ring control (12 WS2812 LEDs)
    "LED_RING_COLOR": (20, 19, 12, "rw", "uint32"),
    
    # GPO control for button monitoring and LED power
    "GPO_READ_VALUES": (20, 0, 5, "ro", "uint8"),   # [X0D11, X0D30, X0D31, X0D33, X0D39]
    "GPO_WRITE_VALUE": (20, 1, 2, "wo", "uint8"),   # [pin_index, value]
}


class _ReSpeaker:
    """Low-level USB control wrapper for XVF3800 parameters."""

    TIMEOUT_MS = 100_000
    VID = 0x2886
    PID = 0x001A

    def __init__(self, dev: "usb.core.Device") -> None:  # type: ignore[name-defined]
        self.dev = dev

    # CRITICAL FIX: Add context manager support
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
        return False

    def close(self) -> None:
        """Release any underlying libusb resources (best-effort)."""
        if hasattr(self, 'dev') and self.dev is not None:
            try:
                usb.util.dispose_resources(self.dev)
            except Exception as e:
                _LOGGER.debug("Error disposing USB resources: %s", e)
            finally:
                self.dev = None

    # ------------------------------------------------------------------
    # Encoding / decoding helpers
    # ------------------------------------------------------------------

    def _pack_values(self, data_type: str, values: Sequence[int]) -> bytes:
        if data_type == "uint8":
            return bytes([int(v) & 0xFF for v in values])
        if data_type == "uint32":
            return b"".join(struct.pack("<I", int(v) & 0xFFFFFFFF) for v in values)
        if data_type == "int32":
            return b"".join(struct.pack("<i", int(v)) for v in values)
        raise ValueError(f"Unsupported data type '{data_type}'")

    def _unpack_values(self, data_type: str, raw: bytes, count: int) -> List[int]:
        if data_type == "uint8":
            return list(raw[:count])
        if data_type == "uint32":
            return list(struct.unpack("<" + "I" * count, raw[: count * 4]))
        if data_type == "int32":
            return list(struct.unpack("<" + "i" * count, raw[: count * 4]))
        raise ValueError(f"Unsupported data type '{data_type}'")

    def _read_length(self, data_type: str, count: int) -> int:
        # +1 for status byte returned by XMOS vendor control read
        if data_type == "uint8":
            return count + 1
        if data_type in ("uint32", "int32"):
            return (count * 4) + 1
        raise ValueError(f"Unsupported data type '{data_type}'")

    # ------------------------------------------------------------------
    # Public parameter IO
    # ------------------------------------------------------------------

    def write(self, name: str, data_list: Sequence[int]) -> None:
        try:
            resid, cmdid, count, access, data_type = PARAMETERS[name]
        except KeyError as exc:
            raise ValueError(f"Unknown XVF3800 parameter '{name}'") from exc

        if access == "ro":
            raise ValueError(f"{name} is read-only")

        if len(data_list) != count:
            raise ValueError(f"{name} expects {count} values, got {len(data_list)}")

        payload = self._pack_values(data_type, data_list)

        _LOGGER.debug(
            "XVF3800 write: name=%s resid=%s cmdid=%s payload_len=%d",
            name,
            resid,
            cmdid,
            len(payload),
        )

        self.dev.ctrl_transfer(
            usb.util.CTRL_OUT
            | usb.util.CTRL_TYPE_VENDOR
            | usb.util.CTRL_RECIPIENT_DEVICE,
            0,
            cmdid,
            resid,
            payload,
            self.TIMEOUT_MS,
        )

    def read(self, name: str, max_retries: int = 10) -> List[int]:
        try:
            resid, cmdid, count, access, data_type = PARAMETERS[name]
        except KeyError as exc:
            raise ValueError(f"Unknown XVF3800 parameter '{name}'") from exc

        if access == "wo":
            raise ValueError(f"{name} is write-only")

        length = self._read_length(data_type, count)
        wValue = 0x80 | cmdid  # per XMOS protocol: read is (0x80 | cmdid)

        attempt = 0
        while True:
            attempt += 1
            resp = self.dev.ctrl_transfer(
                usb.util.CTRL_IN
                | usb.util.CTRL_TYPE_VENDOR
                | usb.util.CTRL_RECIPIENT_DEVICE,
                0,
                wValue,
                resid,
                length,
                self.TIMEOUT_MS,
            )

            if not resp:
                raise RuntimeError("Empty response from XVF3800 control read")

            status = int(resp[0])
            if status == CONTROL_SUCCESS:
                raw = bytes(resp[1:])
                return self._unpack_values(data_type, raw, count)

            if status == SERVICER_COMMAND_RETRY and attempt < max_retries:
                continue

            raise RuntimeError(f"XVF3800 control read failed (status={status}, name={name})")


def _find_device(vid: int = _ReSpeaker.VID, pid: int = _ReSpeaker.PID) -> Optional[_ReSpeaker]:
    dev = usb.core.find(idVendor=vid, idProduct=pid)
    if not dev:
        return None
    return _ReSpeaker(dev)


class XVF3800USBDevice:
    """Small helper for issuing non-LED XVF3800 control commands via PyUSB.

    This is intentionally minimal and does *not* depend on xvf_host.py.
    """

    def __init__(self, vid: int = _ReSpeaker.VID, pid: int = _ReSpeaker.PID):
        self._rsp = _find_device(vid=vid, pid=pid)
        if self._rsp is None:
            raise RuntimeError(
                f"XVF3800 USB device not found (vid=0x{vid:04x}, pid=0x{pid:04x})"
            )

    def close(self) -> None:
        if self._rsp is not None:
            try:
                self._rsp.close()
            except Exception:
                pass
            finally:
                self._rsp = None

    # CRITICAL FIX: Add context manager support
    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensures USB resources are released."""
        self.close()
        return False

    # --- Device control -----------------------------------------------------

    def reboot(self) -> None:
        """Reboot the XVF3800 firmware (resets parameters to defaults)."""
        self._rsp.write("REBOOT", [1])

    def save_configuration(self) -> None:
        """Persist current runtime settings to flash."""
        self._rsp.write("SAVE_CONFIGURATION", [1])

    # --- Audio routing ------------------------------------------------------

    def set_audio_mgr_op_l(self, category: int, source: int) -> None:
        """Set the L output channel source selection (category, source)."""
        self._rsp.write("AUDIO_MGR_OP_L", [int(category) & 0xFF, int(source) & 0xFF])

    def set_audio_mgr_op_r(self, category: int, source: int) -> None:
        """Set the R output channel source selection (category, source)."""
        self._rsp.write("AUDIO_MGR_OP_R", [int(category) & 0xFF, int(source) & 0xFF])

    # --- Wait helpers -------------------------------------------------------

    @staticmethod
    def wait_for_reenumeration(
        vid: int = _ReSpeaker.VID,
        pid: int = _ReSpeaker.PID,
        timeout_s: float = 12.0,
        settle_s: float = 1.0,
    ) -> None:
        """Wait for the XVF3800 to disappear and then reappear on USB."""
        start = time.time()
        # Wait for it to disappear (best effort)
        while time.time() - start < timeout_s:
            if usb.core.find(idVendor=vid, idProduct=pid) is None:
                break
            time.sleep(0.1)

        # Wait for it to reappear
        while time.time() - start < timeout_s:
            if usb.core.find(idVendor=vid, idProduct=pid) is not None:
                break
            time.sleep(0.1)

        # Give kernel/userspace audio stack a moment to settle
        if settle_s > 0:
            time.sleep(settle_s)


class XVF3800LedBackend:
    """High-level LED backend for the XVF3800."""

    ring_led_count: int = 12
    
    # GPO indices (from XVF3800 documentation)
    GPO_WS2812_POWER_INDEX = 3  # X0D33 in GPO_READ_VALUES response

    def __init__(self, vid: int = _ReSpeaker.VID, pid: int = _ReSpeaker.PID) -> None:
        wrapper = _find_device(vid, pid)
        if wrapper is None:
            raise RuntimeError(
                f"XVF3800 USB device not found (vid=0x{vid:04x}, pid=0x{pid:04x})"
            )

        self._dev = wrapper
        self.supports_per_led: bool = False
        
        # CRITICAL FIX: Ensure WS2812 LED power is enabled BEFORE any LED operations
        # This prevents intermittent LED failures caused by X0D33 being low at startup
        try:
            _LOGGER.debug("Ensuring XVF3800 WS2812 LED power (X0D33) is enabled")
            self._dev.write("GPO_WRITE_VALUE", [33, 1])  # X0D33 = high
            time.sleep(0.05)  # Give firmware time to settle
            _LOGGER.info("XVF3800 WS2812 LED power enabled")
        except Exception as e:
            _LOGGER.warning(
                "Could not enable WS2812 LED power, LEDs may not work reliably: %s", e
            )

        # Best-effort feature detection: if we can read LED_RING_COLOR, we assume
        # per-LED control is supported by the current firmware.
        try:
            _ = self._dev.read("LED_RING_COLOR")
            self.supports_per_led = True
        except Exception:
            self.supports_per_led = False

        # Best-effort version read (not fatal)
        try:
            ver = self._dev.read("VERSION")
            _LOGGER.info(
                "Connected to XVF3800 USB device (vid=0x%04x, pid=0x%04x, version=%s, per_led=%s)",
                vid,
                pid,
                ".".join(str(int(x)) for x in ver[:3]),
                self.supports_per_led,
            )
        except Exception:
            _LOGGER.info(
                "Connected to XVF3800 USB device (vid=0x%04x, pid=0x%04x, per_led=%s)",
                vid,
                pid,
                self.supports_per_led,
            )

    # ---------------------------------------------------------------------
    # Helper: Ensure LED power before critical operations
    # ---------------------------------------------------------------------
    
    def _ensure_led_power(self) -> bool:
        """Ensure WS2812 LED power is enabled before operations.
        
        This provides belt-and-suspenders protection against the LED power
        being disabled by firmware or button interactions.
        
        Returns:
            bool: True if power is confirmed on, False if check failed
        """
        try:
            # Read GPO values
            values = self._dev.read("GPO_READ_VALUES")
            if len(values) > self.GPO_WS2812_POWER_INDEX:
                ws2812_power = bool(values[self.GPO_WS2812_POWER_INDEX])
                if not ws2812_power:
                    _LOGGER.warning("WS2812 LED power was off, re-enabling")
                    self._dev.write("GPO_WRITE_VALUE", [33, 1])
                    time.sleep(0.01)  # Brief settle time
                    return True
                return True
        except Exception as e:
            _LOGGER.debug("Could not verify WS2812 LED power state: %s", e)
            return False
        return False

    # ---------------------------------------------------------------------
    # Legacy/global controls
    # ---------------------------------------------------------------------

    def set_effect(self, effect_id: int) -> None:
        """Set LED effect mode (0=off, 1=breath, 2=rainbow, 3=single color, 4=doa)."""
        # Ensure power before effect change
        self._ensure_led_power()
        self._dev.write("LED_EFFECT", [int(effect_id) & 0xFF])

    def set_brightness(self, brightness_0_255: int) -> None:
        """Set LED brightness (0-255)."""
        value = max(0, min(255, int(brightness_0_255)))
        self._dev.write("LED_BRIGHTNESS", [value])

    def set_speed(self, speed_id: int) -> None:
        """Set LED effect speed (0=slow, 1=medium, 2=fast)."""
        self._dev.write("LED_SPEED", [int(speed_id) & 0xFF])

    def set_color(self, r: int, g: int, b: int) -> None:
        """Set LED color for breath / single color modes (0xRRGGBB)."""
        r = max(0, min(255, int(r)))
        g = max(0, min(255, int(g)))
        b = max(0, min(255, int(b)))
        color_value = (r << 16) | (g << 8) | b
        self._dev.write("LED_COLOR", [color_value])

    # ---------------------------------------------------------------------
    # Per-LED ring control (newer firmware)
    # ---------------------------------------------------------------------

    def set_ring_colors(self, color_values: Sequence[int]) -> None:
        """Set all 12 ring LEDs with 0xRRGGBB values (length must be 12)."""
        if not self.supports_per_led:
            raise RuntimeError("Per-LED ring control is not supported by this firmware")
        if len(color_values) != self.ring_led_count:
            raise ValueError(
                f"LED_RING_COLOR expects {self.ring_led_count} values, got {len(color_values)}"
            )
        
        # Ensure power before writing ring colors
        self._ensure_led_power()
        self._dev.write("LED_RING_COLOR", [int(v) & 0xFFFFFFFF for v in color_values])

    def set_ring_rgb(self, colors: Sequence[Tuple[int, int, int]]) -> None:
        """Set all 12 ring LEDs with (r,g,b) tuples (length must be 12)."""
        if len(colors) != self.ring_led_count:
            raise ValueError(
                f"Ring expects {self.ring_led_count} colors, got {len(colors)}"
            )
        vals: List[int] = []
        for r, g, b in colors:
            r = max(0, min(255, int(r)))
            g = max(0, min(255, int(g)))
            b = max(0, min(255, int(b)))
            vals.append((r << 16) | (g << 8) | b)
        self.set_ring_colors(vals)

    def set_ring_solid(self, r: int, g: int, b: int) -> None:
        """Convenience: set all ring LEDs to the same RGB color."""
        self.set_ring_rgb([(r, g, b)] * self.ring_led_count)

    def clear_ring(self) -> None:
        """Convenience: turn all ring LEDs off (per-LED mode)."""
        if not self.supports_per_led:
            # Legacy fallback: just set effect off
            self.set_effect(0)
            self.set_brightness(0)
            return
        self.set_ring_colors([0] * self.ring_led_count)

    def get_version(self) -> Optional[Tuple[int, int, int]]:
        """Return (major, minor, patch) if readable, else None."""
        try:
            ver = self._dev.read("VERSION")
            if len(ver) >= 3:
                return (int(ver[0]), int(ver[1]), int(ver[2]))
        except Exception:
            return None
        return None

    def close(self) -> None:
        if self._dev is not None:
            self._dev.close()
