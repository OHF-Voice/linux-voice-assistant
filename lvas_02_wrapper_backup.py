import uuid
import sys
import runpy

# 1. Patch the MAC address to end in 'b'
FAKE_MAC = 0x68ecc5e46e2b
uuid.getnode = lambda: FAKE_MAC

# 2. Force the arguments to LANE 02 settings
# We hard-code port 6054 and Mic/Sink 02 here
sys.argv = [
    "lvas_02",
    "--name", "LVAS_02",
    "--host", "192.168.1.158",       # Use --host instead of --uri
    "--port", "6054",                # Keep the port separate
    "--wake-model", "alexa",
    "--wakeup-sound", "/home/stef/halfknock.wav",
    "--debug"
]

# 3. Launch
print("Wrapper: Forcing Lane 02 (Port 6054, MAC ..:2b)")
runpy.run_module("linux_voice_assistant", run_name="__main__")
