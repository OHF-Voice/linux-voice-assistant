# Home Assistant Communication with LVA via ESPHome Native API

This document explains how Home Assistant (HA) communicates with Linux Voice Assistant (LVA) over the ESPHome native API on port 6053. It's written for newcomers who want to understand the system architecture without needing deep technical knowledge.

---

## Table of Contents

1. [Overview](#overview)
2. [Connection Lifecycle](#connection-lifecycle)
3. [Message Flow](#message-flow)
4. [Entity Model and Routing](#entity-model-and-routing)
5. [Configuration Update Pipeline](#configuration-update-pipeline)
6. [Troubleshooting Checklist](#troubleshooting-checklist)
7. [Known Limitations](#known-limitations)
8. [Glossary](#glossary)

---

## Overview

### What Connects to What?

```
┌─────────────────────┐                                     ┌─────────────────────┐
│   Home Assistant    │         ESPHome Native API          │  Linux Voice        │
│   (homeassistant)   │  ◄──────────────────────────────►   │  Assistant (LVA)    │
│                     │         Port 6053 (TCP)             │  (LVA host)         │
└─────────────────────┘                                     └─────────────────────┘
```

- **Home Assistant (HA)**: The smart home hub that controls your devices
- **Linux Voice Assistant (LVA)**: A voice assistant running on a Raspberry Pi
- **Port 6053**: The TCP port where LVA listens for incoming API connections from HA

### What is "ESPHome Native API"?

ESPHome is typically used for ESP32/ESP8266 microcontrollers, but its **native API** is a standardized protocol for communicating between Home Assistant and "voice satellite" devices. This API defines:

1. **Connection handshake** - How devices introduce themselves
2. **Entity discovery** - What capabilities the device has (media players, switches, selects)
3. **Command/control** - How HA sends commands (play music, set volume, toggle mute)
4. **State updates** - How LVA reports its current state back to HA
5. **Voice pipeline events** - Real-time events during voice processing (wake word detected, STT done, TTS playing)

LVA implements this protocol in pure Python, allowing a Linux device to behave like an ESPHome voice satellite.

### Key Components Involved

| Component       | File                           | Purpose                                                                       |
|-----------------|--------------------------------|-------------------------------------------------------------------------------|
| `api_server.py` | `APIServer` class              | Low-level protocol handling (packet framing, message encoding/decoding)       |
| `satellite.py`  | `VoiceSatelliteProtocol` class | High-level message handling and routing                                       |
| `entity.py`     | Various entity classes         | MediaPlayerEntity, MuteSwitchEntity, Select entities                          |
| `__main__.py`   | Server startup                 | Creates asyncio server on port 6053                                           |

---

## Connection Lifecycle

### 1. Startup (LVA Side)

When LVA starts on the Raspberry Pi:

```
1. Load preferences from preferences.json
2. Initialize audio players (music player, TTS player)
3. Load wake word models
4. Create VoiceSatelliteProtocol instance
5. Bind to port 6053 (configurable via --port argument)
6. Register via mDNS/Zeroconf for auto-discovery
7. Enter asyncio event loop, waiting for connections
```

The server uses Python's `asyncio` framework to handle multiple connections concurrently.

### 2. HA Connects to LVA

When you add LVA as an ESPHome device in Home Assistant:

```
┌─────────────────────┐                       ┌─────────────────────┐
│ Home Assistant      │                       │ Linux Voice         │
│                     │                       │ Assistant           │
│                     │                       │                     │
│  1. TCP Connect     │ ────────────────────► │                     │
│                     │    to port 6053       │                     │
│                     │                       │                     │
│                     │ ◄──────────────────── │  2. Accept          │
│                     │    connection         │                     │
│                     │                       │                     │
│  3. HelloRequest    │ ────────────────────► │                     │
│     (client info)   │                       │                     │
│                     │                       │                     │
│                     │ ◄──────────────────── │  4. HelloResponse   │
│                     │    (API version,      │     (API version,   │
│                     │     device name)      │     device name)    │
│                     │                       │                     │
│  5. AuthRequest     │ ────────────────────► │                     │
│     (no password)   │                       │                     │
│                     │                       │                     │
│                     │ ◄──────────────────── │  6. AuthResponse    │
│                     │    (success)          │     (accepted)      │
│                     │                       │                     │
│  7. DeviceInfoReq   │ ────────────────────► │                     │
│                     │                       │                     │
│                     │ ◄──────────────────── │  8. DeviceInfoResp  │
│                     │    (name, version,    │     (capabilities)  │
│                     │     features)         │                     │
│                     │                       │                     │
│  9. ListEntitiesReq │ ────────────────────► │                     │
│                     │                       │                     │
│                     │ ◄──────────────────── │ 10. Entity list     │
│                     │    (media player,     │     (key, name,     │
│                     │     mute switch,      │     features)       │
│                     │     selects...)       │                     │
│                     │                       │                     │
│ 11. SubscribeStates │ ────────────────────► │                     │
│     Request         │                       │                     │
│                     │                       │                     │
│                     │ ◄──────────────────── │ 12. Current states  │
│                     │    (volume=0.8,       │     (volume, mute,  │
│                     │     muted=false...)   │     active wake     │
│                     │                       │     words, etc.)    │
└─────────────────────┘                       └─────────────────────┘
```

### 3. Active Connection

While connected:

- **HA periodically sends PingRequest** → LVA responds with PingResponse (keeps connection alive)
- **LVA sends events** when voice pipeline state changes (wake word detected, TTS playing, etc.)
- **HA sends commands** when you interact with HA entities (media controls, switches, selects)

### 4. Disconnect

When the connection is lost:

```
1. connection_lost() is called in satellite.py
2. Set state.connected = False
3. Stop any playing audio
4. Reset pipeline state
5. Log "Disconnected from Home Assistant; waiting for reconnection"
6. Server continues running, waiting for new connections
```

**Important**: The LVA service keeps running even when HA disconnects. It will automatically accept a new connection when HA reconnects.

### 5. Reconnection

HA automatically attempts to reconnect when:

- Network connectivity is restored
- LVA service restarts
- You manually refresh the HA integration

On reconnection, HA goes through the full handshake again (Hello → Auth → DeviceInfo → ListEntities → SubscribeStates).

---

## Message Flow

### High-Level Sequence: Voice Command

```
┌──────────────┐                 ┌──────────────┐                 ┌──────────────┐
│  User says   │                 │  LVA Pi      │                 │   Home       │
│  wake word   │                 │  (LVA)       │                 │  Assistant   │
└──────┬───────┘                 └──────┬───────┘                 └──────┬───────┘
       │                                │                                │
       │       Wake word detected       │                                │
       │───────────────────────────────►│                                │
       │                                │      VoiceAssistantRequest     │
       │                                │     (start=True, wake_word)    │
       │                                │───────────────────────────────►│
       │                                │                                │
       │                                │      VoiceAssistantEvent       │
       │                                │          (RUN_START)           │
       │                                │◄───────────────────────────────│
       │                                │                                │
       │                                │       [Audio streaming]        │
       │                                │◄───────────────────────────────│
       │                                │                                │
       │                                │      VoiceAssistantEvent       │
       │                                │    (STT_END, INTENT_START)     │
       │                                │◄───────────────────────────────│
       │                                │                                │
       │                                │      VoiceAssistantEvent       │
       │                                │         (TTS_START)            │
       │                                │◄───────────────────────────────│
       │                                │                                │
       │           TTS plays            │                                │
       │◄───────────────────────────────│                                │
       │                                │                                │
       │                                │      VoiceAssistantEvent       │
       │                                │          (RUN_END)             │
       │                                │◄───────────────────────────────│
       │                                │                                │
       │                                │ VoiceAssistantAnnounceFinished │
       │                                │───────────────────────────────►│
       └────────────────────────────────┴────────────────────────────────┘
```

### Key Message Types

| Message Type                          | Direction | Purpose                                                                       |
|---------------------------------------|-----------|-------------------------------------------------------------------------------|
| `HelloRequest/Response`               | Both      | Protocol version exchange on connect                                          |
| `AuthenticationRequest/Response`      | Both      | No-password authentication                                                    |
| `DeviceInfoRequest/Response`          | HA→LVA    | Get device name, version, capabilities                                        |
| `ListEntitiesRequest`                 | HA→LVA    | Request list of all entities (media player, switches, selects)                |
| `ListEntitiesXXXResponse`             | LVA→HA    | Entity definitions with keys, names, options                                  |
| `SubscribeHomeAssistantStatesRequest` | HA→LVA    | Subscribe to state updates                                                    |
| `XXXStateResponse`                    | LVA→HA    | Current state of each entity                                                  |
| `MediaPlayerCommandRequest`           | HA→LVA    | Play, pause, stop, volume, mute                                               |
| `SwitchCommandRequest`                | HA→LVA    | Toggle mute switch, thinking sound                                            |
| `SelectCommandRequest`                | HA→LVA    | Select wake word library, sensitivity                                         |
| `VoiceAssistantSetConfiguration`      | HA→LVA    | Set active wake words                                                         |
| `VoiceAssistantEventResponse`         | LVA→HA    | Pipeline events (wake word, STT, TTS, etc.)                                   |
| `VoiceAssistantAnnounceRequest`       | HA→LVA    | TTS announcement from HA                                                      |
| `PingRequest/Response`                | Both      | Keep-alive heartbeats                                                         |

---

## Entity Model and Routing

### What is an Entity?

In this context, an **entity** is a named capability that HA can interact with. LVA exposes several entity types:

| Entity Type                  | Key | Description                                              |
|------------------------------|-----|----------------------------------------------------------|
| Media Player                 | 1   | Music playback, TTS, volume control                      |
| Mute Switch                  | 2   | Toggle microphone on/off                                 |
| Thinking Sound Switch        | 3   | Toggle "thinking" sound when processing                  |
| Wake Word Library Select     | 4   | Choose microWakeWord vs openWakeWord                     |
| Wake Word Sensitivity Select | 5   | Choose sensitivity level                                 |

### How Routing Works (Key-Based)

Each entity has a unique integer `key`. When HA sends a command, it includes the key:

```
SelectCommandRequest:
  key: 4           ← Which entity (wake word library)
  state: "openWakeWord"  ← New value to set
```

LVA's `satellite.py` routes this to the correct entity:

```python
# satellite.py, lines 470-483
elif isinstance(msg, SelectCommandRequest):
    # Route SelectCommandRequest by key to matching entity only
    msg_key = msg.key
    for entity in self.state.entities:
        if hasattr(entity, "key") and entity.key == msg_key:
            yield from entity.handle_message(msg)
            break
```

**Why this matters**: Earlier versions of LVA had a bug where commands were broadcast to ALL entities (each entity would respond). This caused issues where multiple entities might handle the same command. The key-based routing fixes this by ensuring only the entity with the matching key processes the command.

### Entity State Synchronization

When HA reconnects or requests states:

1. HA sends `SubscribeHomeAssistantStatesRequest`
2. LVA iterates through all entities
3. Each entity's `handle_message()` returns its current state
4. LVA sends `XXXStateResponse` messages back to HA

This ensures HA always has an accurate view of LVA's state.

---

## Configuration Update Pipeline

### How HA Changes Become LVA Changes

When you change a setting in Home Assistant (e.g., select a different wake word library):

```
┌─────────────────────┐          ┌─────────────────────┐          ┌─────────────────────┐
│ Home Assistant      │          │ LVA API Server      │          │ LVA Internal        │
│ (UI or automations) │          │ (satellite.py)      │          │ (models.py,         │
│                     │          │                     │          │ preferences.json)   │
└──────────┬──────────┘          └──────────┬──────────┘          └──────────┬──────────┘
           │                                │                                │
           │      SelectCommandRequest      │                                │
           │ (key=4, state="microWakeWord") │                                │
           │───────────────────────────────►│                                │
           │                                │                                │
           │                                │    _on_wake_word_library_set   │
           │                                │───────────────────────────────►│
           │                                │                                │
           │                                │       state.preferences.       │
           │                                │    wake_word_library = ...     │
           │                                │       save_preferences()       │
           │                                │───────────────────────────────►│
           │                                │                                │
           │                                │      scan_wake_words_for_      │
           │                                │            library()           │
           │                                │───────────────────────────────►│
           │                                │                                │
           │      SelectStateResponse       │                                │
           │ (key=4, state="microWakeWord") │                                │
           │◄───────────────────────────────│                                │
           │                                │        _deferred_library_      │
           │                                │           reconnect()          │
           │                                │        (0.5s delay, then       │
           │                                │        close transport)        │
           │                                │◄───────────────────────────────│
           │                                │                                │
           │        [HA reconnects]         │                                │
           │◄───────────────────────────────│                                │
           │                                │                                │
           └────────────────────────────────┴────────────────────────────────┘
```

### Preference Persistence

Settings like:
- Volume level
- Muted state
- Active wake words
- Wake word library
- Wake word sensitivity
- Thinking sound enabled

Are stored in `preferences.json` on the Raspberry Pi. Changes are persisted immediately via `save_preferences()`.

---

## Troubleshooting Checklist

### On the Raspberry Pi

#### 1. Is the LVA Service Running?

```bash
systemctl --user status linux-voice-assistant
```

Expected output shows `active (running)`. If not running, check logs.

#### 2. Is Port 6053 Listening?

```bash
ss -tlnp | grep 6053
```

Expected: `LISTEN` on `0.0.0.0:6053` or `<IP>:6053`

#### 3. Check Recent Logs

```bash
journalctl --user -u linux-voice-assistant -n 100 --no-pager
```

Look for these key log lines:

| Log Line                                                     | Meaning                                           |
|--------------------------------------------------------------|---------------------------------------------------|
| `Server started (host=X, port=6053)`                         | Server is running and listening                   |
| `Authentication successful, connected to Home Assistant`     | HA successfully connected                         |
| `Disconnected from Home Assistant; waiting for reconnection` | HA disconnected                                   |
| `Invalid preamble:`                                          | Protocol error, possible incompatible API version |
| `address already in use`                                     | Port 6053 already in use                          |
| `All N attempts failed to bind on address`                   | Cannot start server                               |

#### 4. Check Audio Devices

```bash
wpctl status
```

Ensure default source (microphone) and sink (speaker) are set correctly.

### On Home Assistant

#### 1. Check HA Logs

In Home Assistant, go to **Settings → System → Logs** and look for:
- ESPHome connection errors
- Messages about the device name
- API version mismatches

#### 2. Verify Integration Status

In **Settings → Devices & Services**, find the ESPHome integration and check:
- Device status (online/offline)
- Last connected time
- Entities list

### Common Failure Modes

| Symptom                                 | Likely Cause             | Fix                                              |
|-----------------------------------------|--------------------------|--------------------------------------------------|
| "Connection reset by peer"              | LVA service not running  | `systemctl --user restart linux-voice-assistant` |
| "Connection refused"                    | Port 6053 not listening  | Check `ss -tlnp | grep 6053`                     |
| "EOF" / "Connection closed"             | LVA crashed or restarted | Check `journalctl` for errors                    |
| Entities missing in HA                  | Full reconnect needed    | Restart HA integration or LVA service            |
| Wake word library change doesn't appear | HA needs to reconnect    | Entity dropdowns refresh on reconnect            |
| Volume changes don't persist            | Preference save failed   | Check file permissions on `preferences.json`     |

---

## Known Limitations

### 1. Single Active Connection (Observed)

The current implementation supports **one active HA connection** at a time. Multiple HA instances connecting simultaneously is not supported.

*Source: `satellite.py` - single `VoiceSatelliteProtocol` instance per server*

### 2. No Native Encryption (Design Limitation)

By default, LVA uses plaintext communication on port 6053. For production deployments, consider:
- Running LVA on an isolated network
- Using a VPN tunnel
- HA's proxy functionality for encryption

*Source: `api_server.py` - no TLS handling in current implementation*

### 3. Wake Word Library Change Triggers Reconnect

When you change the wake word library via the HA select entity:
1. The change is saved
2. LVA closes the connection after 0.5 seconds
3. HA automatically reconnects

This is intentional to refresh HA's entity dropdown options.

*Source: `satellite.py` - `_deferred_library_reconnect()` method*

### 4. Limited to Two Active Wake Words

The ESPHome API supports a maximum of 2 active wake words (slot 0 and slot 1). This is a protocol limitation.

*Source: `satellite.py` - `max_active_wake_words=2` in VoiceAssistantConfigurationResponse*

### 5. No Password Authentication

LVA currently accepts all connections without password authentication. This is suitable for trusted local networks only.

*Source: `api_server.py` - `AuthenticationResponse()` sent without verification*

---

## Glossary

| Term              | Definition                                                                |
|-------------------|---------------------------------------------------------------------------|
| **API**           | Application Programming Interface - a way for two programs to communicate |
| **asyncio**       | Python library for asynchronous I/O operations                            |
| **Entity**        | A controllable device or capability in Home Assistant                     |
| **ESPHome**       | A system to control microcontrollers (like ESP32) from Home Assistant     |
| **Key**           | Unique integer identifier for an entity                                   |
| **mDNS/Zeroconf** | Network auto-discovery protocol                                           |
| **Protobuf**      | Protocol Buffers - binary serialization format used by ESPHome API        |
| **Satellite**     | A voice assistant device that relays commands to Home Assistant           |
| **STT**           | Speech-to-Text - converting spoken words to text                          |
| **TTS**           | Text-to-Speech - converting text to spoken audio                          |
| **varuint**       | Variable-length unsigned integer encoding                                 |

---

## Quick Reference Commands

```bash
# Check LVA service status
systemctl --user status linux-voice-assistant

# Restart LVA service
systemctl --user restart linux-voice-assistant

# View recent logs
journalctl --user -u linux-voice-assistant -n 80

# Check if port 6053 is listening
ss -tlnp | grep 6053

# Check audio devices
wpctl status

# View preferences file
cat /opt/linux-voice-assistant/preferences.json
```
