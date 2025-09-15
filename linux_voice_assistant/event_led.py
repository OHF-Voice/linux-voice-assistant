import logging
from typing import Any, Callable

from .event_bus import EventHandler, subscribe

_LOGGER = logging.getLogger(__name__)



"""Controls the LEDs on the ReSpeaker 2mic HAT."""
from math import ceil
from typing import Tuple

import time
import asyncio
import gpiozero
import spidev


NUM_LEDS = 3
LEDS_GPIO = 12
RGB_MAP = {
    "rgb": [3, 2, 1],
    "rbg": [3, 1, 2],
    "grb": [2, 3, 1],
    "gbr": [2, 1, 3],
    "brg": [1, 3, 2],
    "bgr": [1, 2, 3],
}



_OFF = (0, 0, 0)
_WHITE = (255, 255, 255)
_RED = (255, 0, 0)
_YELLOW = (255, 255, 0)
_BLUE = (0, 0, 255)
_GREEN = (0, 255, 0)

# -----------------------------------------------------------------------------


class APA102:
    """
    Driver for APA102 LEDS (aka "DotStar").
    (c) Martin Erzberger 2016-2017
    """

    # Constants
    MAX_BRIGHTNESS = 0b11111  # Safeguard: Set to a value appropriate for your setup
    LED_START = 0b11100000  # Three "1" bits, followed by 5 brightness bits

    def __init__(
        self,
        num_led,
        global_brightness,
        loop: asyncio.AbstractEventLoop,
        order="rgb",
        bus=0,
        device=1,
        max_speed_hz=8000000,
    ):
        self.num_led = num_led  # The number of LEDs in the Strip
        order = order.lower()
        self.rgb = RGB_MAP.get(order, RGB_MAP["rgb"])
        # Limit the brightness to the maximum if it's set higher
        if global_brightness > self.MAX_BRIGHTNESS:
            self.global_brightness = self.MAX_BRIGHTNESS
        else:
            self.global_brightness = global_brightness
        print("LED brightness:", self.global_brightness)

        self.leds = [self.LED_START, 0, 0, 0] * self.num_led  # Pixel buffer
        self.spi = spidev.SpiDev()  # Init the SPI device
        self.spi.open(bus, device)  # Open SPI port 0, slave device (CS) 1
        # Up the speed a bit, so that the LEDs are painted faster
        if max_speed_hz:
            self.spi.max_speed_hz = max_speed_hz

        self.current_task = None
        self.loop = loop


    def clock_start_frame(self):
        """Sends a start frame to the LED strip.

        This method clocks out a start frame, telling the receiving LED
        that it must update its own color now.
        """
        self.spi.xfer2([0] * 4)  # Start frame, 32 zero bits

    def clock_end_frame(self):
        """Sends an end frame to the LED strip.

        As explained above, dummy data must be sent after the last real colour
        information so that all of the data can reach its destination down the line.
        The delay is not as bad as with the human example above.
        It is only 1/2 bit per LED. This is because the SPI clock line
        needs to be inverted.

        Say a bit is ready on the SPI data line. The sender communicates
        this by toggling the clock line. The bit is read by the LED
        and immediately forwarded to the output data line. When the clock goes
        down again on the input side, the LED will toggle the clock up
        on the output to tell the next LED that the bit is ready.

        After one LED the clock is inverted, and after two LEDs it is in sync
        again, but one cycle behind. Therefore, for every two LEDs, one bit
        of delay gets accumulated. For 300 LEDs, 150 additional bits must be fed to
        the input of LED one so that the data can reach the last LED.

        Ultimately, we need to send additional numLEDs/2 arbitrary data bits,
        in order to trigger numLEDs/2 additional clock changes. This driver
        sends zeroes, which has the benefit of getting LED one partially or
        fully ready for the next update to the strip. An optimized version
        of the driver could omit the "clockStartFrame" method if enough zeroes have
        been sent as part of "clockEndFrame".
        """

        self.spi.xfer2([0xFF] * 4)

        # Round up num_led/2 bits (or num_led/16 bytes)
        # for _ in range((self.num_led + 15) // 16):
        #    self.spi.xfer2([0x00])

    def set_pixel(self, led_num, red, green, blue, bright_percent=100):
        """Sets the color of one pixel in the LED stripe.

        The changed pixel is not shown yet on the Stripe, it is only
        written to the pixel buffer. Colors are passed individually.
        If brightness is not set the global brightness setting is used.
        """
        if led_num < 0:
            return  # Pixel is invisible, so ignore
        if led_num >= self.num_led:
            return  # again, invisible

        # Calculate pixel brightness as a percentage of the
        # defined global_brightness. Round up to nearest integer
        # as we expect some brightness unless set to 0
        brightness = int(ceil(bright_percent * self.global_brightness / 100.0))

        # LED startframe is three "1" bits, followed by 5 brightness bits
        ledstart = (brightness & 0b00011111) | self.LED_START

        start_index = 4 * led_num
        self.leds[start_index] = ledstart
        self.leds[start_index + self.rgb[0]] = red
        self.leds[start_index + self.rgb[1]] = green
        self.leds[start_index + self.rgb[2]] = blue

    def set_pixel_rgb(self, led_num, rgb_color, bright_percent=100):
        """Sets the color of one pixel in the LED stripe.

        The changed pixel is not shown yet on the Stripe, it is only
        written to the pixel buffer.
        Colors are passed combined (3 bytes concatenated)
        If brightness is not set the global brightness setting is used.
        """
        self.set_pixel(
            led_num,
            (rgb_color & 0xFF0000) >> 16,
            (rgb_color & 0x00FF00) >> 8,
            rgb_color & 0x0000FF,
            bright_percent or self.global_brightness,
        )

    def rotate(self, positions=1):
        """Rotate the LEDs by the specified number of positions.

        Treating the internal LED array as a circular buffer, rotate it by
        the specified number of positions. The number could be negative,
        which means rotating in the opposite direction.
        """
        cutoff = 4 * (positions % self.num_led)
        self.leds = self.leds[cutoff:] + self.leds[:cutoff]

    def show(self):
        """Sends the content of the pixel buffer to the strip.

        Todo: More than 1024 LEDs requires more than one xfer operation.
        """
        self.clock_start_frame()
        # xfer2 kills the list, unfortunately. So it must be copied first
        # SPI takes up to 4096 Integers. So we are fine for up to 1024 LEDs.
        data = list(self.leds)
        while data:
            self.spi.xfer2(data[:32])
            data = data[32:]
        self.clock_end_frame()

    def cleanup(self):
        """Release the SPI device; Call this method at the end"""

        self.spi.close()  # Close SPI port


    def run_action(self, action_mentod_name: str, *args: Any) -> None:
        if self.current_task and not self.current_task.done():
            self.current_task.cancel()

        self.current_task = asyncio.run_coroutine_threadsafe(getattr(self, action_mentod_name)(*args), self.loop)

    async def color(self, rgb: Tuple[int, int, int], brightness = None) -> None:
        for i in range(NUM_LEDS):
            self.set_pixel(i, rgb[0], rgb[1], rgb[2], brightness or self.global_brightness)

        self.show()

    async def blink(self, color, count=10000):
        for _ in range(count):
            await self.color(color)
            await asyncio.sleep(0.3)
            await self.color(_OFF)
            await asyncio.sleep(0.3)

    async def pulse(self, color: Tuple[int, int, int], speed: float = 0.009):
        """Asynchronously pulses the LEDs from off to full brightness and back."""
        # Fade in
        while(1):
            for brightness in range(1, self.global_brightness+1):
                await self.color(color, brightness)
                await asyncio.sleep(speed)

            # Fade out
            for brightness in range(self.global_brightness+1, 0, -1):
                await self.color(color, brightness)
                await asyncio.sleep(speed)




class LedEvent(EventHandler):
    def __init__(self, state):
        super().__init__(state)
        self.leds = APA102(num_led=3, global_brightness=31, loop=self.state.loop)

    @subscribe
    def ready(self, data: dict):
        _LOGGER.debug('ready LED green blink')
        self.leds.run_action("blink", _GREEN, 3)

    @subscribe
    def voice_wakeword(self, data: dict):
        self.leds.run_action("pulse", _BLUE)

    @subscribe
    def voice_VOICE_ASSISTANT_STT_VAD_END(self, data: dict):
        self.leds.run_action("pulse", _YELLOW)

    @subscribe
    def voice_play_tts(self, data: dict):
        self.leds.run_action("pulse", _GREEN)

    # This event fires long before the TTS is done speaking
    # @subscribe
    # def voice_VOICE_ASSISTANT_RUN_END(self, data: dict):
    #     self.leds.run_action("color", _OFF, 0)

    @subscribe
    def voice__tts_finished(self, data: dict):
        self.leds.run_action("color", _OFF, 0)
