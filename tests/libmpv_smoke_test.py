import time
import mpv

"""
mpv_smoke_test.py - test playback of media
"""

def handle_event(event):
    print(f"EVENT: {event['event_id']}")


player = mpv.MPV(
    audio_display=False,
    log_handler=print,
    loglevel="info",
)

player.event_callback = handle_event

print("Loading media...")
player.play("https://icecast.radiofrance.fr/fip-midfi.mp3")

time.sleep(5)

print("Pausing...")
player.pause = True
time.sleep(2)

print("Resuming...")
player.pause = False
time.sleep(5)

print("Stopping...")
player.stop()

time.sleep(2)
print("Done.")
