#!/usr/bin/env python3
"""
Single-service LED pattern switcher that listens to LVA journal events and switches patterns directly.
"""
import subprocess
import threading
import re
import time
from state_patterns import all_off_stoppable, pulse_wave, cylon_bounce, color_cycle_breathing, all_off
import glob
import os

# List of systemd user unit names to monitor (hardcoded)
PREF_USER_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "preferences", "user")
LVA_SERVICES = [
    os.path.basename(f) for f in glob.glob(os.path.join(PREF_USER_DIR, "*.service"))
]

# Map LVA event types to pattern functions and arguments
EVENT_TO_PATTERN = {
    "VOICE_ASSISTANT_RUN_START": (color_cycle_breathing, (), {'color': (50,50,0)}), # Yellow breathing (listening)
    "VOICE_ASSISTANT_INTENT_START": (cylon_bounce, ()),                           # Purple Cylon Bounce
    "VOICE_ASSISTANT_TTS_START": (color_cycle_breathing, (), {'color': (0,50,0)}),# Green breathing
    "TTS_RESPONSE_FINISHED": (all_off_stoppable, ()),                             # All Off (precise end)
}

LVA_EVENT_RE = re.compile(r"LVA_EVENT: (\w+)")

class PatternThread(threading.Thread):
    def __init__(self, pattern_func, *args, **kwargs):
        super().__init__()
        self.pattern_func = pattern_func
        self.args = args
        self.kwargs = kwargs
        self._stop_event = threading.Event()
        self.daemon = True

    def run(self):
        self.pattern_func(*self.args, stop_event=self._stop_event, **self.kwargs)

    def stop(self):
        self._stop_event.set()

import select


def short_breath(color=(0,0,50), duration=2.0):
    import threading, time
    stop_event = threading.Event()
    t = threading.Thread(target=color_cycle_breathing, kwargs={'stop_event': stop_event, 'color': color})
    t.start()
    time.sleep(duration)
    stop_event.set()
    t.join()

def main():
    if not LVA_SERVICES:
        print("No LVA instance services found in preferences/user. Exiting.")
        return
    print(f"Listening for LVA events from {', '.join(LVA_SERVICES)} and switching LED patterns...")
    current_thread = None
    # Startup: 2 second blue breath, then all off
    short_breath((0,0,50), duration=2.0)
    all_off()

    # Build journalctl command with multiple --user-unit args
    journalctl_cmd = ["journalctl"]
    for svc in LVA_SERVICES:
        journalctl_cmd += ["--user-unit", svc]
    journalctl_cmd += ["-f", "-n", "0", "-o", "cat"]
    proc = subprocess.Popen(journalctl_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)

    # Track which services have been seen (by event line)
    seen_services = set()
    try:
        for line in proc.stdout:
            # Try to extract the service name from the journal line (systemd appends it)
            svc_name = None
            for svc in LVA_SERVICES:
                if svc in line:
                    svc_name = svc
                    break
            match = LVA_EVENT_RE.search(line)
            if svc_name and svc_name not in seen_services:
                print(f"[LED] LVA service ready: {svc_name}")
                seen_services.add(svc_name)
            if match:
                event = match.group(1)
                print(f"[LED] Detected event: {event}")
                pattern_info = EVENT_TO_PATTERN.get(event)
                if pattern_info:
                    if current_thread and current_thread.is_alive():
                        current_thread.stop()
                        current_thread.join()
                    func, args, *kwargs = pattern_info
                    kwargs = kwargs[0] if kwargs else {}
                    current_thread = PatternThread(func, *args, **kwargs)
                    current_thread.start()
    except KeyboardInterrupt:
        print("Exiting LED journal pattern service.")
    finally:
        if current_thread and current_thread.is_alive():
            current_thread.stop()
            current_thread.join()
        all_off()
        proc.terminate()
        proc.wait()

if __name__ == "__main__":
    main()
