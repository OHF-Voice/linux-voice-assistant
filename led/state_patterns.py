BRIGHTNESS = 0.1 # 10% brightness (change this value to adjust overall brightness)
import spidev
import time
import math

spi = spidev.SpiDev()
spi.open(1, 0)
spi.max_speed_hz = 4000000

NUM_LEDS = 8

def send_colors(colors):
    tx_data = []
    # Always set first LED as a very dim indicator (e.g., dim blue)
    indicator = (0, 0, 3)
    out_colors = [indicator] + list(colors)
    for idx, (r, g, b) in enumerate(out_colors):
        r = int(r * BRIGHTNESS)
        g = int(g * BRIGHTNESS)
        b = int(b * BRIGHTNESS)
        for val in [g, r, b]:  # GRB order
            for i in range(8):
                tx_data.append(0xE0 if (val << i) & 0x80 else 0x80)
    spi.xfer2(tx_data)

def all_off():
    send_colors([(0, 0, 0)] * NUM_LEDS)

def all_off_stoppable(stop_event=None):
    all_off()
    # Wait until stop_event is set, so thread can be joined cleanly
    while not (stop_event and stop_event.is_set()):
        time.sleep(0.05)

# --- Pattern Functions (continuous, blocking) ---
def cylon_bounce(stop_event=None):
    tail = [24, 8, 2]
    color = (40, 0, 40)
    try:
        while not (stop_event and stop_event.is_set()):
            for i in range(NUM_LEDS * 2 - 2):
                if stop_event and stop_event.is_set():
                    return
                leds = [(0, 0, 0)] * NUM_LEDS
                pos = i if i < NUM_LEDS else (NUM_LEDS * 2 - 2 - i)
                leds[pos] = color
                for t, b in enumerate(tail, 1):
                    if pos - t >= 0:
                        leds[pos - t] = (int(color[0]*b/40), 0, int(color[2]*b/40))
                    if pos + t < NUM_LEDS:
                        leds[pos + t] = (int(color[0]*b/40), 0, int(color[2]*b/40))
                send_colors(leds)
                time.sleep(0.07)
    except KeyboardInterrupt:
        all_off()

def pulse_wave(stop_event=None, color=(50,50,0)):
    min_brightness = 8
    import math
    try:
        phase = 0
        while not (stop_event and stop_event.is_set()):
            leds = []
            for i in range(NUM_LEDS):
                b = int(21 * math.sin((i + phase) * 2 * math.pi / NUM_LEDS) + 29)
                r = int(color[0] * b / 50)
                g = int(color[1] * b / 50)
                b_ = int(color[2] * b / 50)
                leds.append((max(r, min_brightness), max(g, min_brightness), max(b_, min_brightness)))
            send_colors(leds)
            phase = (phase + 1) % NUM_LEDS
            time.sleep(0.09)
    except KeyboardInterrupt:
        all_off()

def solid_red(stop_event=None):
    try:
        while not (stop_event and stop_event.is_set()):
            send_colors([(50, 0, 0)] * NUM_LEDS)
            time.sleep(0.2)
    except KeyboardInterrupt:
        all_off()

def color_cycle_breathing(stop_event=None, color=None):
    min_brightness = 8
    try:
        while not (stop_event and stop_event.is_set()):
            colors = [color] if color else [(50,0,0), (0,50,0), (0,0,50)]
            for c in colors:
                for i in range(min_brightness, 51, 2):
                    if stop_event and stop_event.is_set():
                        return
                    send_colors([(int(c[0]*i/50), int(c[1]*i/50), int(c[2]*i/50))] * NUM_LEDS)
                    time.sleep(0.02)
                for i in range(50, min_brightness-1, -2):
                    if stop_event and stop_event.is_set():
                        return
                    send_colors([(int(c[0]*i/50), int(c[1]*i/50), int(c[2]*i/50))] * NUM_LEDS)
                    time.sleep(0.02)
    except KeyboardInterrupt:
        all_off()

if __name__ == "__main__":
    try:
        print("1: Processing (Cylon Bounce, purple)\n2: Listening (Pulse Wave, yellow)\n3: Responding (Pulse Wave, green)\n4: Mute (Solid Red)\n5: Startup (Color Cycle Breathing)\n0: All Off\nq: Quit")
        while True:
            choice = input("Select pattern: ").strip()
            if choice == "1":
                cylon_bounce()
            elif choice == "2":
                pulse_wave((50,50,0))  # yellow
            elif choice == "3":
                pulse_wave((0,50,0))   # green
            elif choice == "4":
                solid_red()
            elif choice == "5":
                color_cycle_breathing()
            elif choice == "0":
                all_off()
            elif choice.lower() == "q":
                all_off()
                break
    except KeyboardInterrupt:
        all_off()
    finally:
        spi.close()
