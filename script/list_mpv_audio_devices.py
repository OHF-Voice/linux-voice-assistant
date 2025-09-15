#!/usr/bin/env python3
import subprocess

def list_devices(ao="pulse"):
    result = subprocess.run(
        ["mpv", f"--ao={ao}", "--audio-device=help"],
        capture_output=True,
        text=True
    )
    print(result.stdout)

if __name__ == "__main__":
    print("PulseAudio devices:")
    list_devices("pulse")
    print("\nALSA devices:")
    list_devices("alsa")

