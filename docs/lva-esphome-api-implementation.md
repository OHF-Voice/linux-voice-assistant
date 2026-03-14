# LVA ESPHome Native API Implementation Specification

This document describes the exact implementation of the ESPHome Native API server-side in Linux Voice Assistant (LVA). It complements the novice guide and provides detailed engineering specifications including message contracts, state machines, routing rules, invariants, error handling, and extension points.

---

## 1. Purpose & Scope

This spec covers the LVA server implementation that exposes an ESPHome-native API endpoint to Home Assistant. The implementation:

- Listens on TCP port 6053 (configurable)
- Presents LVA as a voice satellite device
- Handles entity discovery, state synchronization, and voice pipeline events
- Supports media player, switch, and select entities for configuration

**Out of Scope:**
- Client-side ESPHome API behavior (HA side)
- Audio processing pipeline (wake word detection, STT, TTS)
- Wake word model loading/management details
- Hardware audio configuration

---

## 2. Implementation Overview (Modules/Classes)

| File            | Class/Function                    | Purpose                                                                      |
|-----------------|-----------------------------------|------------------------------------------------------------------------------|
| `api_server.py` | `APIServer`                       | Base asyncio.Protocol for low-level transport, framing, and message dispatch |
| `api_server.py` | `process_packet()`                | Message type routing, basic message handling (Hello, Auth, Ping, Disconnect) |
| `satellite.py`  | `VoiceSatelliteProtocol`          | Subclass of APIServer; handles voice-assistant specific messages             |
| `entity.py`     | `ESPHomeEntity`                   | Abstract base for all entity types                                           |
| `entity.py`     | `MediaPlayerEntity`               | Media playback control (play, pause, stop, volume, mute)                     |
| `entity.py`     | `MuteSwitchEntity`                | Mute toggle switch                                                           |
| `entity.py`     | `ThinkingSoundEntity`             | Thinking sound enable/disable switch                                         |
| `entity.py`     | `WakeWordLibrarySelectEntity`     | Select entity for wake word library choice                                   |
| `entity.py`     | `WakeWordSensitivitySelectEntity` | Select entity for wake word sensitivity                                      |
| `models.py`     | `ServerState`                     | Runtime state container (entities, preferences, players)                     |
| `models.py`     | `Preferences`                     | Persisted user preferences (volume, muted, wake words)                       |
| `__main__.py`   | `main()`                          | Server startup, argument parsing, asyncio server creation                    |

---

## 3. Transport & Framing

### 3.1 Transport Layer
- **Protocol**: Python `asyncio.Protocol` over TCP
- **Default Port**: 6053 (configurable via `--port`)
- **Binding**: Binds to `--host` IP address (default: auto-detected IPv4 of `--network-interface`)

### 3.2 Framing Protocol
LVA uses the ESPHome Native API binary framing format:

```
[preamble: varuint = 0x00] [length: varuint] [message_type: varuint] [protobuf_payload: bytes]
```

**Framing Rules:**
1. **Preamble**: Always `0x00` (1 byte varuint)
2. **Length**: Varuint encoding of protobuf message length
3. **Message Type**: Varuint mapping to protobuf message type (via `MESSAGE_TYPE_TO_PROTO`)
4. **Payload**: Raw protobuf serialized bytes

**Varuint Encoding:**
- 7 bits per byte for data
- MSB indicates continuation (1 = more bytes follow, 0 = last byte)
- Little-endian base-128 encoding

### 3.3 Encoding/Decoding
- **Inbound**: `data_received()` accumulates bytes, parses varuints, extracts payload, uses protobuf `ParseFromString()`
- **Outbound**: `send_messages()` serializes via `SerializeToString()`, uses `make_plain_text_packets()` for framing

**Reference:** `api_server.py` lines 102-192 (framing), lines 80-90 (serialization)

---

## 4. Connection & Handshake State Machine

### 4.1 State Diagram (Text)

```
                    +------------------+
                    |   LISTENING      |
                    |  (port bound)    |
                    +--------+---------+
                             |
                      TCP connection
                             |
                             v
                    +------------------+
         +--------->|    CONNECTED     |
         |          +--------+---------+
         |                   |
    HelloRequest             |
         |                   v
         |          +------------------+
         |          |    HELLO_SENT    |
         |          | (sent response)  |
         |          +--------+---------+
         |                   |
    AuthRequest              |
         |                   v
         |          +------------------+
         |          |  AUTHENTICATED   |
         |          | (sent response)  |
         |          +--------+---------+
         |                   |
         |     ListEntities + SubscribeStates
         |                   |
         |                   v
         +---------+------------------+
                   |    CONNECTED     |
                   | (full operation) |
                   +--------+---------+
                            |
                     DisconnectRequest
                            |
                            v
                   +------------------+
                   |   DISCONNECTED   |
                   +------------------+
```

### 4.2 Handshake Sequence

1. **TCP Connect** → `connection_made()` called
2. **Hello**:
   - Client sends `HelloRequest` (client info)
   - Server responds with `HelloResponse(api_version_major=1, api_version_minor=10, name=<device_name>)`
3. **Authentication**:
   - Client sends `AuthenticationRequest` (password optional, LVA uses `uses_password=False`)
   - Server responds with `AuthenticationResponse()` (no password required)
   - On success: `state.connected = True`
4. **Entity Sync**:
   - Server sends all entity states via `SubscribeHomeAssistantStatesRequest` handling
   - Each entity returns its current state message

### 4.3 Connection Lifecycle Details

| Event                   | Trigger           | Action                                               |
|-------------------------|-------------------|------------------------------------------------------|
| `connection_made`       | TCP accept        | Initialize buffer, transport, event loop thread      |
| `data_received`         | Data on socket    | Parse frames, dispatch to `process_packet()`         |
| `AuthenticationRequest` | After Hello       | Set `state.connected = True`, send all entity states |
| `DisconnectRequest`     | Client disconnect | Send `DisconnectResponse`, close transport           |
| `connection_lost`       | TCP close         | Cleanup state, reset flags, await reconnect          |

**Reference:** `satellite.py` lines 689-719 (connection_lost cleanup)

---

## 5. Supported Message Contracts

### 5.1 Core Protocol Messages

| Message                  | Direction | Required Fields                                  | Behavior                                 |
|--------------------------|-----------|--------------------------------------------------|------------------------------------------|
| `HelloRequest`           | C→S       | `client_info`                                    | Respond with `HelloResponse` (API v1.10) |
| `HelloResponse`          | S→C       | `api_version_major`, `api_version_minor`, `name` | Sent on Hello                            |
| `AuthenticationRequest`  | C→S       | `password` (optional)                            | Accept (no password), respond            |
| `AuthenticationResponse` | S→C       | none (empty)                                     | Sent on Auth success                     |
| `DisconnectRequest`      | C→S       | none                                             | Respond, close transport                 |
| `DisconnectResponse`     | S→C       | none                                             | Sent before close                        |
| `PingRequest`            | C→S       | none                                             | Respond with `PingResponse`              |
| `PingResponse`           | S→C       | none                                             | Keepalive response                       |
| `DeviceInfoRequest`      | C→S       | none                                             | Respond with `DeviceInfoResponse`        |

### 5.2 Entity Discovery Messages

| Message                               | Direc | Req Fields | Behavior                                                                           |
|---------------------------------------|-------|------------|------------------------------------------------------------------------------------|
| `ListEntitiesRequest`                 | C→S   | none       | Each entity yields its `ListEntities*Response`; finally `ListEntitiesDoneResponse` |
| `SubscribeHomeAssistantStatesRequest` | C→S   | none       | Each entity yields its current state                                               |

### 5.3 Voice Assistant Messages

| Message                               | Direction | Required Fields             | Behavior                                       |
|---------------------------------------|-----------|-----------------------------|------------------------------------------------|
| `VoiceAssistantRequest`               | S→C       | `start`, `wake_word_phrase` | Initiate voice pipeline                        |
| `VoiceAssistantEventResponse`         | S→C       | `event_type`, `data`        | Pipeline events (start, intent, stt, tts, end) |
| `VoiceAssistantAnnounceRequest`       | C→S       | `media_id`                  | TTS announcement                               |
| `VoiceAssistantAnnounceFinished`      | S→C       | none                        | Announcement complete                          |
| `VoiceAssistantTimerEventResponse`    | C→S       | `event_type`                | Timer events                                   |
| `VoiceAssistantConfigurationRequest`  | C→S       | `external_wake_words`       | Query available wake words                     |
| `VoiceAssistantConfigurationResponse` | S→C       | `available_wake_words`,     | Wake word config                               |
|                                                   | `active_wake_words`,        |                                                |
|                                                   | `max_active_wake_words`     |                                                |
| `VoiceAssistantSetConfiguration`      | C→S       | `active_wake_words`         | Update active wake words                       |
| `VoiceAssistantAudio`                 | S→C       | `data`                      | Stream audio chunks to HA                      |

### 5.4 Entity Command Messages

| Message                     | Direction | Required Fields                                    | Behavior                                    |
|-----------------------------|-----------|----------------------------------------------------|---------------------------------------------|
| `MediaPlayerCommandRequest` | C→S       | `key`, optionally `command`, `media_url`, `volume` | Media control (play/pause/stop/mute/volume) |
| `SelectCommandRequest`      | C→S       | `key`, `state`                                     | Select entity command (routed by key)       |
| `SwitchCommandRequest`      | C→S       | `key`, `state`                                     | Switch entity command (routed by key)       |

**Reference:** `satellite.py` lines 426-585 (message handling)

---

## 6. Entity Routing Contract

### 6.1 Key-Based Dispatch

Two message types use **key-based routing** (unicast to matching entity):

- `SelectCommandRequest`
- `SwitchCommandRequest`

**Algorithm:**
```
for entity in state.entities:
    if hasattr(entity, "key") and entity.key == msg.key:
        yield from entity.handle_message(msg)
        break
```

### 6.2 Broadcast Dispatch

Other messages broadcast to **all entities**:

- `ListEntitiesRequest` → each entity yields its description
- `SubscribeHomeAssistantStatesRequest` → each entity yields its state
- `MediaPlayerCommandRequest` → each entity may handle (MediaPlayerEntity only)

### 6.3 Entity Keys

Keys are assigned at `VoiceSatelliteProtocol` initialization based on `len(state.entities)` at creation time. The order is:

1. MediaPlayerEntity (always key 0 if present, else first available)
2. MuteSwitchEntity
3. ThinkingSoundEntity
4. WakeWordLibrarySelectEntity
5. WakeWordSensitivitySelectEntity

**Reference:** `satellite.py` lines 66-208 (entity initialization)

---

## 7. Configuration Update Pipeline

### 7.1 Wake Word Configuration

1. **Query**: HA sends `VoiceAssistantConfigurationRequest`
2. **Response**: LVA returns `VoiceAssistantConfigurationResponse` with:
   - `available_wake_words`: All discovered + external models
   - `active_wake_words`: Ordered list from preferences
   - `max_active_wake_words`: 2

3. **Update**: HA sends `VoiceAssistantSetConfiguration`
   - LVA processes each wake word ID
   - Loads model if not already loaded (may download external)
   - Persists ordered list to preferences
   - Sets `state.wake_words_changed = True` to trigger reload in audio thread

### 7.2 Select Entity Configuration

- `WakeWordLibrarySelectEntity`: Dynamic options from `discover_wake_word_libraries()`
- `WakeWordSensitivitySelectEntity`: Fixed options (Model default, Very/Moderately/Slightly sensitive)

### 7.3 Volume/Mute Persistence

- Volume changes persist via `Preferences` JSON file
- Mute state persists across restarts

**Reference:** `satellite.py` lines 533-585 (wake word config), `models.py` lines 110-139 (preferences)

---

## 8. Observability (Logging)

### 8.1 Log Levels

| Level   | Usage                                                            |
|---------|------------------------------------------------------------------|
| `ERROR` | Framing errors (invalid preamble, length, message type)          |
| `INFO`  | Connection events (connected, disconnected), major state changes |
| `DEBUG` | Message details, entity state messages, audio events             |

### 8.2 Key Log Points

- `api_server.py:114` - Incorrect preamble
- `api_server.py:118` - Incorrect length
- `api_server.py:122` - Incorrect message type
- `satellite.py:719` - Disconnected from HA
- `satellite.py:726` - Authentication successful

**Reference:** Search for `_LOGGER.` in source files

---

## 9. Failure Modes & Recovery

### 9.1 Transport Errors

| Failure                           | Handling                        |
|-----------------------------------|---------------------------------|
| Invalid preamble (not 0x00)       | Log error, terminate connection |
| Invalid varuint (buffer underrun) | Log error, terminate connection |
| Unknown message type              | No response (message dropped)   |
| Protobuf parse failure            | No response (message dropped)   |

### 9.2 Connection Loss

On `connection_lost`:
1. Set `state.connected = False`
2. Clear audio streaming flags
3. Stop music/TTS players
4. Reset wake word pipeline
5. Sync mute switch entity state
6. Log: "Disconnected from Home Assistant; waiting for reconnection"

### 9.3 Auto-Reconnect

- LVA listens indefinitely on port
- HA initiates reconnect automatically
- On reconnect: full entity sync repeats

### 9.4 Port Binding

- Retries up to 15 times with 1-second delay if port in use
- Logs warning on each retry, exits on final failure

**Reference:** `__main__.py` lines 413-441 (port bind retry)

---

## 10. Compatibility Notes

### 10.1 ESPHome API Version

- **Advertised**: API version 1.10
- **Supported Features** (via `DeviceInfoResponse.voice_assistant_feature_flags`):
  - `VOICE_ASSISTANT` - Voice assistant support
  - `API_AUDIO` - Audio streaming
  - `ANNOUNCE` - TTS announcements
  - `START_CONVERSATION` - Continue conversation after TTS
  - `TIMERS` - Timer support

### 10.2 Client Compatibility

- Works with Home Assistant ESPHome integration
- No password authentication (expects `uses_password=False`)
- Expects HA to handle voice pipeline orchestration

### 10.3 Known Limitations

- Single media player entity only (extras removed)
- Single mute switch only
- Wake word library limited to discovered directories
- HA's 1-item `active_wake_words` list limitation (no slot indices)

---

## 11. Extension Points

### 11.1 Adding New Message Support

To add support for a new ESPHome message type:

1. **Import** the protobuf message class from `aioesphomeapi.api_pb2`
2. **Handle** in `VoiceSatelliteProtocol.handle_message()`:
   ```python
   elif isinstance(msg, NewMessageType):
       # Process and yield response(s)
       yield NewResponse(...)
   ```
3. **Return** iterable of response messages (can be empty)

### 11.2 Adding New Entity Types

1. **Subclass** `ESPHomeEntity` in `entity.py`
2. **Implement** `handle_message()` to handle:
   - `ListEntitiesRequest` → yield entity description
   - `SubscribeHomeAssistantStatesRequest` → yield state
   - Command messages as appropriate
3. **Register** in `VoiceSatelliteProtocol.__init__()`:
   - Create instance with unique key
   - Append to `state.entities`

### 11.3 Adding New Voice Events

Handle in `VoiceSatelliteProtocol.handle_voice_event()`:
- Match on `VoiceAssistantEventType` enum
- Update internal state
- Optionally send `VoiceAssistantEventResponse` back to HA

---

## Appendix: Key Code Touchpoints

| File            | Class/Function           | Lines   | Description           |
|-----------------|--------------------------|---------|-----------------------|
| `api_server.py` | `APIServer`              | 30-192  | Base protocol class   |
| `api_server.py` | `process_packet`         | 47-78   | Message dispatch      |
| `api_server.py` | `data_received`          | 102-135 | Frame parsing         |
| `api_server.py` | `send_messages`          | 80-90   | Message serialization |
| `satellite.py`  | `VoiceSatelliteProtocol` | 57-801  | Voice protocol        |
| `satellite.py`  | `handle_message`         | 426-585 | Message handling      |
| `satellite.py`  | `process_packet`         | 721-738 | Auth + state sync     |
| `satellite.py`  | `connection_lost`        | 689-719 | Cleanup on disconnect |
| `entity.py`     | `ESPHomeEntity`          | 43-49   | Entity base           |
| `entity.py`     | `MediaPlayerEntity`      | 55-244  | Media player entity   |
| `entity.py`     | `MuteSwitchEntity`       | 249-299 | Mute switch           |
| `models.py`     | `ServerState`            | 69-139  | Runtime state         |
| `models.py`     | `Preferences`            | 59-67   | User preferences      |
| `__main__.py`   | `main`                   | 41-464  | Server startup        |
