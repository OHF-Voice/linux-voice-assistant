# Peripheral WebSocket API

Linux Voice Assistant exposes a WebSocket API on port 6055 that lets external scripts and containers integrate with LVA in real time. This is how LED rings, button boards, displays, and other peripherals communicate with the voice assistant without modifying LVA itself.

The peripheral API is what allows hardware like the Satellite1 HAT Board or the ReSpeaker series to light up when you say the wake word, show a thinking animation while the assistant processes your request, and let physical buttons start a conversation or dismiss a timer — all from a completely separate process or container.

---

## How it works

LVA acts as the WebSocket **server**. Your peripheral script or container connects as a **client**. Once connected, LVA immediately sends a snapshot of its current state, and from that point on all communication is bidirectional:

- **Events** flow from LVA to your client, describing what the assistant is doing right now (listening, thinking, speaking, etc.).
- **Commands** flow from your client to LVA, telling it what to do (start listening, adjust volume, fire a button press event to Home Assistant, etc.).

All messages are JSON objects sent over a plain WebSocket connection. No authentication is required.

```
Your peripheral script                   LVA
        │                                  │
        │──── WebSocket connect ──────────►│
        │◄─── snapshot (current state) ────│
        │                                  │
        │◄─── event: wake_word_detected ───│   (user said the wake word)
        │◄─── event: listening ────────────│   (STT streaming started)
        │◄─── event: thinking ─────────────│   (HA is processing)
        │◄─── event: tts_speaking ─────────│   (assistant is replying)
        │◄─── event: tts_finished ─────────│
        │◄─── event: idle ─────────────────│
        │                                  │
        │──── command: volume_up ─────────►│   (user pressed a button)
        │──── command: start_listening ───►│   (user pressed action button)
```

### Connection

```
ws://<lva-host>:6055
```

By default LVA binds to `0.0.0.0:6055`. You can change the host and port with `--peripheral-host` and `--peripheral-port`, or disable the API entirely with `--disable-peripheral-api`.

### Message format

Events from LVA:
```json
{"event": "<event_name>", "data": {"key": "value"}}
```

Commands to LVA:
```json
{"command": "<command_name>", "data": {"key": "value"}}
```

The `data` field is omitted when there is no payload.

### Snapshot on connect

Immediately after connecting, LVA sends a snapshot so your client can synchronise its state without waiting for the next event:

```json
{
  "event": "snapshot",
  "data": {
    "muted": false,
    "volume": 0.8,
    "ha_connected": true,
    "last_stt_text": "set a timer for five minutes",
    "last_tts_text": "Sure, I've set a timer for five minutes."
  }
}
```

`last_stt_text` and `last_tts_text` are the most recent conversation exchange, or `null` if no conversation has happened yet since startup. Both are cleared when a new pipeline run starts.

---

## Python quick-start

```python
import asyncio
import json
import websockets

async def main():
    async with websockets.connect("ws://localhost:6055") as ws:
        async for raw in ws:
            msg = json.loads(raw)
            event = msg.get("event")
            data  = msg.get("data", {})
            print(f"Event: {event}  data={data}")

            # Example: send a command in response to an event
            if event == "idle":
                await ws.send(json.dumps({"command": "volume_up"}))

asyncio.run(main())
```

---

## Events (LVA → your script)

These are all the events LVA emits. Your peripheral script receives them and reacts — for example by changing LED colours, updating a display, or playing a sound.

### Voice pipeline events

| Event | Data | Description |
|-------|------|-------------|
| `wake_word_detected` | — | The configured wake word was detected. The wakeup chime is now playing. Start your "wake" animation here. |
| `listening` | — | The wakeup chime has finished and LVA is now streaming audio to Home Assistant for speech-to-text. Show a "listening" animation. |
| `stt_text` | `{"text": str}` | Home Assistant returned the recognised speech transcript. Use this to show what the user said on a display or LED ticker. |
| `thinking` | — | LVA has stopped streaming audio and Home Assistant is processing the intent. Show a "thinking" animation. |
| `tts_text` | `{"text": str}` | Home Assistant returned the assistant's text response, just before TTS audio begins playing. Use this to display the reply on a screen. |
| `tts_speaking` | — | The TTS audio response is now playing. Show a "speaking" animation. |
| `tts_finished` | — | TTS playback has finished. Transition back to idle, unless a new pipeline run is about to start. |
| `idle` | — | The assistant is idle and ready for the next wake word. Turn off active animations. |

### Error and connection events

| Event | Data | Description |
|-------|------|-------------|
| `pipeline_error` | `{"reason": str}` | The voice pipeline failed — for example STT failure or an intent error. Show a brief red error animation (3 flashes, then off). NOT emitted when HA disconnects; see `disconnected` below. |
| `disconnected` | — | The TCP connection to Home Assistant was lost. Show a "no connection" animation and keep it until you see `zeroconf` with `status: connected`. Note: if LVA itself is not running, your client will see a WebSocket connection failure instead — treat that the same way. |
| `muted` | — | The microphone has been muted. Show a muted indicator on your LEDs (e.g. red at mic positions). |
| `zeroconf` | `{"status": "getting_started" \| "connected"}` | Reports LVA's connection lifecycle. `getting_started` is emitted at startup before HA connects; `connected` is emitted once the HA TCP handshake completes. Use `connected` to clear a "no connection" animation. |

### Timer events

| Event | Data | Description |
|-------|------|-------------|
| `timer_ticking` | `{"id": str, "name": str, "total_seconds": int, "seconds_left": int}` | A timer has been set and is counting down. Show a countdown animation proportional to `seconds_left / total_seconds`. |
| `timer_updated` | `{"id": str, "name": str, "total_seconds": int, "seconds_left": int}` | A running timer was adjusted. Update your countdown display. |
| `timer_ringing` | `{"id": str, "name": str, "total_seconds": int, "seconds_left": int}` | The timer has expired and the alarm sound is playing. Show a repeating alert animation. Send `stop_timer_ringing` to dismiss. |

### Media and volume events

| Event | Data | Description |
|-------|------|-------------|
| `media_player_playing` | — | HA started streaming music or media to the background music player (non-announcement). Show a "media playing" indicator. Not emitted for TTS or announcements — those use `tts_speaking`. |
| `volume_changed` | `{"volume": float}` | The speaker volume changed (0.0–1.0). Update any volume display or indicator ring. |
| `volume_muted` | `{"muted": bool}` | The media player mute state changed. Distinct from microphone mute (`muted` event). |

---

## Commands (your script → LVA)

Send these to control LVA from your peripheral hardware.

### Voice pipeline

| Command | Data | Description |
|---------|------|-------------|
| `start_listening` | — | Play the start-listening chime and begin a voice pipeline run, as if the user pressed the action button. No-op if already muted or pipeline already active. |
| `stop_pipeline` | — | Abort the active voice pipeline at any phase (listening, thinking, or TTS speaking). Cleans up STT streaming, sends `AnnounceFinished` to HA, unducking music, and emits `idle`. |
| `stop_speaking` | — | Stop TTS or announcement playback. Equivalent to `stop_pipeline` for the speaking phase. |

### Microphone

| Command | Data | Description |
|---------|------|-------------|
| `mute_mic` | — | Mute the microphone. Stops any active pipeline, plays the mute sound, and emits `muted` to all clients. No-op if already muted. |
| `unmute_mic` | — | Unmute the microphone. Plays the unmute sound and emits `idle`. No-op if already unmuted. |

### Volume

| Command | Data | Description |
|---------|------|-------------|
| `volume_up` | — | Increase speaker volume by one step (default 5 %, configurable with `--peripheral-volume-step`). Updates HA, persists the value, and emits `volume_changed`. |
| `volume_down` | — | Decrease speaker volume by one step. |
| `set_volume` | `{"volume": float}` | Set the speaker volume to an exact value between 0.0 and 1.0. Values outside this range are clamped. Updates HA, persists, and emits `volume_changed`. Example: `{"command": "set_volume", "data": {"volume": 0.6}}` |

### Timer

| Command | Data | Description |
|---------|------|-------------|
| `stop_timer_ringing` | — | Dismiss a ringing timer. Stops the alarm sound, clears the timer state, unducking music, and emits `idle`. No-op if no timer is ringing. |

### Media player

| Command | Data | Description |
|---------|------|-------------|
| `stop_media_player` | — | Stop background music / media playback. Updates the HA media player entity state to Idle. |
| `pause_media_player` | — | Pause background music. Updates HA state to Paused. |
| `resume_media_player` | — | Resume paused music. Updates HA state to Playing. |

### Button events (sent to Home Assistant)

These commands fire event entities on the LVA device page in Home Assistant, which you can use in automations. They are intended for a dedicated **second button** on your hardware — not the action button that already handles context-sensitive commands like `start_listening`.

| Command | Data | Description |
|---------|------|-------------|
| `button_single_press` | — | Fire a `single_press` event to HA. No sound is played. |
| `button_double_press` | — | Fire a `double_press` event to HA. Plays the configured double-press sound. |
| `button_triple_press` | — | Fire a `triple_press` event to HA. Plays the configured triple-press sound. |
| `button_long_press` | — | Fire a `long_press` event to HA. Plays the configured long-press sound. |

> **Important:** `button_single_press` must be sent from a **different physical button** than the action button. The action button's single press is reserved for triggering the voice pipeline (`start_listening` on first press, then context-sensitive commands on subsequent presses). Using the same button for both would create ambiguous behavior.

The press type is determined by your peripheral script using timing logic that matches the Home Assistant Voice PE centre button behaviour:

```
Single press  : one press, then >250 ms silence
Double press  : press → <250 ms gap → press → >250 ms silence
Triple press  : press → <250 ms gap → press → <250 ms gap → press → >250 ms silence
Long press    : button held for ≥1 s
```

Your script is responsible for detecting these patterns and sending the appropriate command. See the board-specific examples below for reference implementations.

---

## Minimal example script

The following self-contained script connects to LVA, prints every event with a timestamp, and maps a single GPIO button (Raspberry Pi GPIO 17) to context-sensitive commands:

```python
#!/usr/bin/env python3
"""Minimal LVA peripheral — prints events, GPIO button → context command."""

import asyncio
import json
import logging
import websockets

LVA_URI = "ws://localhost:6055"
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")

assist_state = "idle"
muted = False


async def main():
    global assist_state, muted
    command_queue: asyncio.Queue = asyncio.Queue()

    # Optional: wire up a GPIO button with gpiozero
    try:
        from gpiozero import Button
        loop = asyncio.get_running_loop()
        btn = Button(17, pull_up=True, bounce_time=0.15)
        btn.when_pressed = lambda: asyncio.run_coroutine_threadsafe(
            command_queue.put(_context_command()), loop
        )
    except ImportError:
        pass  # Run without GPIO on non-Pi hardware

    while True:
        try:
            async with websockets.connect(LVA_URI) as ws:
                recv = asyncio.create_task(_recv(ws, command_queue))
                send = asyncio.create_task(_send(ws, command_queue))
                done, pending = await asyncio.wait([recv, send], return_when=asyncio.FIRST_EXCEPTION)
                for t in pending:
                    t.cancel()
        except Exception as exc:
            logging.warning("Disconnected: %s — retrying in 3s", exc)
            await asyncio.sleep(3)


def _context_command() -> str:
    if assist_state == "timer_ringing":
        return "stop_timer_ringing"
    if assist_state in ("wake_word_detected", "listening", "thinking"):
        return "stop_pipeline"
    if assist_state == "tts_speaking":
        return "stop_speaking"
    if assist_state == "media_player_playing":
        return "stop_media_player"
    return "start_listening"


async def _recv(ws, queue):
    global assist_state, muted
    async for raw in ws:
        msg = json.loads(raw)
        event = msg.get("event", "")
        data  = msg.get("data", {})
        logging.info("← %s  %s", event, data)
        if event in ("snapshot", "idle", "tts_finished", "wake_word_detected",
                     "listening", "thinking", "tts_speaking", "muted",
                     "timer_ticking", "timer_ringing", "media_player_playing"):
            assist_state = event if event != "snapshot" else ("muted" if data.get("muted") else "idle")
            if event == "snapshot":
                muted = data.get("muted", False)


async def _send(ws, queue):
    while True:
        command = await queue.get()
        logging.info("→ %s", command)
        await ws.send(json.dumps({"command": command}))


asyncio.run(main())
```

---

## Pre-included examples

The repository ships with ready-to-use peripheral controllers for popular hardware boards. Each example is a standalone Docker container that connects to LVA over this API.

- [Peripheral Web Console](https://github.com/OHF-Voice/linux-voice-assistant/blob/main/examples/Peripheral%20web%20console/DOCS.md) — browser-based real-time dashboard showing all events and a command palette; useful for debugging your setup or testing automations without any hardware
- [Satellite1 HAT Board](https://github.com/OHF-Voice/linux-voice-assistant/blob/main/examples/Satellite1%20HAT%20Board/DOCS.md) — 12-LED SK6812 ring + 4 buttons; animations mirror the Home Assistant Voice PE firmware exactly
- [ReSpeaker 2-Mic Pi HAT](https://github.com/OHF-Voice/linux-voice-assistant/blob/main/examples/ReSpeaker%202mic%20HAT/DOCS.md) — 3 APA102 LEDs + 1 onboard button; compact single-button controller
- [ReSpeaker 4-Mic Array HAT](https://github.com/OHF-Voice/linux-voice-assistant/blob/main/examples/ReSpeaker%204mic%20HAT/DOCS.md) — 12 APA102 LEDs + external GPIO buttons; four-microphone circular array
- [ReSpeaker Mic Array v2.0 (USB)](https://github.com/OHF-Voice/linux-voice-assistant/blob/main/examples/ReSpeaker%20Mic%20Array%20v2.0%20(USB)/DOCS.md) — 12 APA102 LEDs driven over USB HID; plug-and-play, no GPIO required
- [Jabra Speak 410](https://github.com/OHF-Voice/linux-voice-assistant/blob/main/examples/Jabra%20Speak%20410/DOCS.md) — USB speakerphone with hardware button integration

---

## Adding your own example

Have you built a peripheral controller for a board not listed above? Contributions are very welcome. Open a pull request at [github.com/OHF-Voice/linux-voice-assistant/pulls](https://github.com/OHF-Voice/linux-voice-assistant/pulls) with:

- A folder under `examples/<Your Board Name>/`
- The controller script (Python or other language)
- A `Dockerfile` and `compose.yml` so others can run it with one command
- A `DOCS.md` following the same structure as the existing examples (hardware layout, GPIO mapping, LED animations, installation steps, troubleshooting)

Please make sure your example handles reconnection gracefully (retry on WebSocket failure) and shows a "not ready" animation when LVA is not reachable or HA is disconnected.