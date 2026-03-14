# ESPHome Native API Full Protocol Specification

> **Version:** This document reflects the ESPHome Native API as of proto version retrieved March 2026.
> The `.proto` files are the authoritative source of truth; this document summarizes them for engineering reference.
> **Source:** [esphome/esphome - api.proto](https://raw.githubusercontent.com/esphome/esphome/main/esphome/components/api/api.proto)
> **Source:** [esphome/esphome - api_options.proto](https://raw.githubusercontent.com/esphome/esphome/main/esphome/components/api/api_options.proto)
> **Docs:** [ESPHome API Component](https://esphome.io/components/api.html)

---

## 1. Scope and Goals

This document provides a comprehensive engineering reference for the ESPHome Native API—the binary protocol used by Home Assistant (and other clients) to communicate with ESPHome devices. It covers:

- **Transport and framing** mechanics (TCP, varint-prefixed protobuf)
- **Connection lifecycle** (Hello, authentication, entity discovery, state subscription)
- **Message catalog** organized by subsystem (connection, entities, Bluetooth, voice assistant, etc.)
- **Entity model** patterns (list → state → command)
- **Extensibility** and version compatibility strategy

This spec describes the **full upstream protocol surface**, not the LVA implementation subset. Engineers can use this document to identify features that could be leveraged for new LVA capabilities.

---

## 2. Terminology

| Term                 | Definition                                                                                               |
|----------------------|----------------------------------------------------------------------------------------------------------|
| **Varint**           | Variable-length integer encoding (unsigned, little-endian base-128). MSB indicates continuation.         |
| **Protobuf**         | Protocol Buffers (proto3 syntax). Messages are serialized with `SerializeToString()`/`ParseFromString()` |
| **Message ID**       | Numeric identifier (1-255) embedded in the framing header, mapped to a protobuf message type.            |
| **Entity**           | A device or functional unit exposed via the API (e.g., sensor, light, switch).                           |
| **List Entities**    | Initial discovery phase where the server enumerates all available entities.                              |
| **Subscribe States** | Ongoing subscription to entity state changes.                                                            |
| **Noise**            | The Noise Protocol Framework used for encrypted connections (replaced password auth in ESPHome 2026.1.0) |

---

## 3. Transport and Framing

### 3.1 Transport Layer

- **Protocol:** TCP socket
- **Default Port:** 6053
- **Binding:** Configurable via `api:` in ESPHome YAML; defaults to all interfaces (`0.0.0.0`)

### 3.2 Framing Format

Each message follows this binary layout:

```
[preamble: varuint = 0x00] [length: varuint] [message_type: varuint] [protobuf_payload: bytes]
```

| Field        | Type      | Description                                               |
|--------------|-----------|-----------------------------------------------------------|
| Preamble     | varuint   | Always `0x00`. Acts as a frame sync byte.                 |
| Length       | varuint   | Size of the protobuf payload in bytes.                    |
| Message Type | varuint   | Message ID (1-255) identifying the protobuf message type. |
| Payload      | bytes     | Serialized protobuf message.                              |

### 3.3 Varint Encoding

- 7 bits per byte for data
- MSB (bit 7) = continuation flag (1 = more bytes follow, 0 = final byte)
- Little-endian order (least significant byte first)

### 3.4 Protobuf Encoding

- All messages are defined in `api.proto` using proto3 syntax
- Serialization: `SerializeToString()`
- Deserialization: `ParseFromString()`
- Message IDs are assigned via the `(id)` option in `api_options.proto`

---

## 4. Connection Lifecycle

### 4.1 State Machine Overview

```
LISTENING → (TCP connect) → CONNECTED → (HelloRequest) → HELLO_SENT
         → (HelloResponse) → AUTHENTICATED → (optional list entities) → READY
```

### 4.2 Connection Sequence

| Step | Client → Server                         | Server → Client                          | Notes                                                 |
|------|-----------------------------------------|------------------------------------------|-------------------------------------------------------|
| 1    | `HelloRequest` (ID 1)                   |                                          | Client announces client_info, api_version_major/minor |
| 2    |                                         | `HelloResponse` (ID 2)                   | Server sends api_version, server_info, name           |
| 3    | `NoiseEncryptionSetKeyRequest` (ID 124) |                                          | **ESPHome 2026.1.0+**: Set Noise key for encryption   |
| 4    |                                         | `NoiseEncryptionSetKeyResponse` (ID 125) | Confirms encryption ready                             |
| 5    | `DeviceInfoRequest` (ID 9)              |                                          | Optional: request device metadata                     |
| 6    |                                         | `DeviceInfoResponse` (ID 10)             | Sends name, mac_address, esphome_version, etc.        |
| 7    | `ListEntitiesRequest` (ID 11)           |                                          | Start entity discovery                                |
| 8    |                                         | Streaming `*ListEntities*Response`       | One message per entity                                |
|      |                                         | (IDs 12-18, 43, 46, etc.)                |                                                       |
| 9    |                                         | `ListEntitiesDoneResponse` (ID 19)       | Signals end of list                                   |
| 10   | `SubscribeStatesRequest` (ID 20)        |                                          | Subscribe to state updates                            |
| 11   | `SubscribeLogsRequest` (ID 28)          |                                          | Optional: subscribe to log messages                   |
| 12   |                                         | Ongoing state updates                    | Server pushes `*StateResponse` messages               |
| 13   | `DisconnectRequest` (ID 5)              |                                          | Graceful disconnect                                   |
| 14   |                                         | `DisconnectResponse` (ID 6)              | Connection closed                                     |

### 4.3 Ping/Pong

Both client and server can send:
- `PingRequest` (ID 7)
- `PingResponse` (ID 8)

Used for keepalive. No authentication required.

---

## 5. Authentication and Encryption

> **Note:** Password authentication (`AuthenticationRequest` / `AuthenticationResponse`, IDs 3-4) was **deprecated and removed in ESPHome 2026.1.0**. These message IDs are reserved and should not be reused.

### 5.1 Noise Protocol Framework

From ESPHome 2026.1.0 onward, encryption is mandatory. The protocol uses the Noise Protocol Framework:

| Message                              | ID   | Direction | Description                           |
|--------------------------------------|------|-----------|---------------------------------------|
| `NoiseEncryptionSetKeyRequest`       | 124  | C→S       | Client sends Noise key material       |
| `NoiseEncryptionSetKeyResponse`      | 125  | S→C       | Server confirms key is accepted       |

The encryption key is typically configured via the `api:` component in ESPHome YAML:

```yaml
api:
  encryption:
    key: "your-32-byte-noise-key-here"
```

**Reference:** [ESPHome API Encryption](https://esphome.io/components/api.html#encryption)

---

## 6. Message Catalog

### 6.1 Connection Management

| Message                  | ID | Direction | Description                                                     |
|--------------------------|----|-----------|-----------------------------------------------------------------|
| `HelloRequest`           | 1  | C→S       | `client_info`, `api_version_major`, `api_version_minor`         |
| `HelloResponse`          | 2  | S→C       | `api_version_major`, `api_version_minor`, `server_info`, `name` |
| `DisconnectRequest`      | 5  | Both      | (empty)                                                         |
| `DisconnectResponse`     | 6  | Both      | (empty)                                                         |
| `PingRequest`            | 7  | Both      | (empty)                                                         |
| `PingResponse`           | 8  | Both      | (empty)                                                         |
| `DeviceInfoRequest`      | 9  | C→S       | (empty)                                                         |
| `DeviceInfoResponse`     | 10 | S→C       | `name`, `mac_address`, `esphome_version`, `model`,              |
|                          |    |           | `has_deep_sleep`, `bluetooth_proxy_feature_flags`,              |
|                          |    |           | `voice_assistant_feature_flags`, etc.                           |

### 6.2 Entity Discovery

| Message                              | ID | Direction | Entity Type                    |
|--------------------------------------|----|-----------|--------------------------------|
| `ListEntitiesRequest`                | 11 | C→S       | (triggers enumeration)         |
| `ListEntitiesBinarySensorResponse`   | 12 | S→C       | Binary sensor                  |
| `ListEntitiesCoverResponse`          | 13 | S→C       | Cover (garage door, blinds)    |
| `ListEntitiesFanResponse`            | 14 | S→C       | Fan                            |
| `ListEntitiesLightResponse`          | 15 | S→C       | Light                          |
| `ListEntitiesSensorResponse`         | 16 | S→C       | Sensor                         |
| `ListEntitiesSwitchResponse`         | 17 | S→C       | Switch                         |
| `ListEntitiesTextSensorResponse`     | 18 | S→C       | Text sensor                    |
| `ListEntitiesDoneResponse`           | 19 | S→C       | (signals end)                  |

### 6.3 State Subscription

| Message                     | ID | Direction | Description                |
|-----------------------------|----|-----------|----------------------------|
| `SubscribeStatesRequest`    | 20 | C→S       | Subscribe to state changes |
| `BinarySensorStateResponse` | 21 | S→C       | Binary sensor state        |
| `CoverStateResponse`        | 22 | S→C       | Cover state                |
| `FanStateResponse`          | 23 | S→C       | Fan state                  |
| `LightStateResponse`        | 24 | S→C       | Light state                |
| `SensorStateResponse`       | 25 | S→C       | Sensor reading             |
| `SwitchStateResponse`       | 26 | S→C       | Switch state               |
| `TextSensorStateResponse`   | 27 | S→C       | Text sensor value          |

### 6.4 Logging

| Message                 | ID | Direction | Description                                             |
|-------------------------|----|-----------|---------------------------------------------------------|
| `SubscribeLogsRequest`  | 28 | C→S       | Subscribe to logs; `level` (enum), `dump_config` (bool) |
| `SubscribeLogsResponse` | 29 | S→C       | Log message: `level`, `message` (bytes)                 |

**Log Levels:** `NONE=0`, `ERROR=1`, `WARN=2`, `INFO=3`, `CONFIG=4`, `DEBUG=5`, `VERBOSE=6`, `VERY_VERBOSE=7`

### 6.5 Entity Commands

| Entity       | Command Message             | ID | Key Fields                                                               |
|--------------|-----------------------------|----|--------------------------------------------------------------------------|
| Cover        | `CoverCommandRequest`       | 30 | `key`, `position`, `tilt`, `stop`                                        |
| Fan          | `FanCommandRequest`         | 31 | `key`, `state`, `oscillating`, `direction`, `speed_level`, `preset_mode` |
| Light        | `LightCommandRequest`       | 32 | `key`, `state`, `brightness`, `color_mode`, `rgb`, `white`,              |
|              |                             |    | `color_temperature`, `transition_length`, `flash_length`, `effect`       |
| Switch       | `SwitchCommandRequest`      | 33 | `key`, `state`                                                           |
| Button       | `ButtonCommandRequest`      | 62 | `key`                                                                    |
| Climate      | `ClimateCommandRequest`     | 48 | `key`, `mode`, `target_temperature`, `fan_mode`, `swing_mode`, `preset`  |
| Number       | `NumberCommandRequest`      | 51 | `key`, `state`                                                           |
| Select       | `SelectCommandRequest`      | 54 | `key`, `state`                                                           |
| Siren        | `SirenCommandRequest`       | 57 | `key`, `state`, `tone`, `duration`, `volume`                             |
| Lock         | `LockCommandRequest`        | 60 | `key`, `command` (LOCK_UNLOCK, LOCK_LOCK, LOCK_OPEN)                     |
| Media Player | `MediaPlayerCommandRequest` | 65 | `key`, `command`, `volume`, `media_url`, `announcement`                  |

### 6.6 Home Assistant Integration

| Message                                 | ID  | Direction | Description                     |
|-----------------------------------------|-----|-----------|---------------------------------|
| `SubscribeHomeassistantServicesRequest` | 34  | C→S       | Receive HA service calls        |
| `HomeassistantServiceMap`               | —   | —         | Key-value pair for service data |
| `HomeassistantActionRequest`            | 35  | S→C       | HA calls a service on ESPHome   |
| `HomeassistantActionResponse`           | 130 | C→S       | Response to HA service call     |
| `SubscribeHomeAssistantStatesRequest`   | 38  | C→S       | Subscribe to HA entity states   |
| `SubscribeHomeAssistantStateResponse`   | 39  | S→C       | HA state change subscription    |
| `HomeAssistantStateResponse`            | 40  | C→S       | Report HA state to ESPHome      |

### 6.7 User-Defined Services

| Message                        | ID  | Direction | Description                      |
|--------------------------------|-----|-----------|----------------------------------|
| `ListEntitiesServicesResponse` | 41  | S→C       | Advertise a user-defined service |
| `ExecuteServiceRequest`        | 42  | C→S       | Call a service on ESPHome        |
| `ExecuteServiceResponse`       | 131 | S→C       | Service execution response       |

**Service Argument Types:** `BOOL`, `INT`, `FLOAT`, `STRING`, `BOOL_ARRAY`, `INT_ARRAY`, `FLOAT_ARRAY`, `STRING_ARRAY`

### 6.8 Camera

| Message                      | ID | Direction | Description                               |
|------------------------------|----|-----------|-------------------------------------------|
| `ListEntitiesCameraResponse` | 43 | S→C       | Camera entity metadata                    |
| `CameraImageRequest`         | 45 | C→S       | Request single frame or stream            |
| `CameraImageResponse`        | 44 | S→C       | Image data chunk; `done=true` signals end |

### 6.9 Time Sync

| Message           | ID | Direction | Description                                      |
|-------------------|----|-----------|--------------------------------------------------|
| `GetTimeRequest`  | 36 | S→C       | ESPHome requests time                            |
| `GetTimeResponse` | 37 | C→S       | Client responds with `epoch_seconds`, `timezone` |

### 6.10 Bluetooth (BLE) Proxy

| Message                                     | ID | Direction | Description                       |
|---------------------------------------------|----|-----------|-----------------------------------|
| `SubscribeBluetoothLEAdvertisementsRequest` | 66 | C→S       | Subscribe to BLE advertisements   |
| `BluetoothLERawAdvertisementsResponse`      | 93 | S→C       | Raw advertisement batch           |
| `BluetoothDeviceRequest`                    | 68 | C→S       | Connect, disconnect, pair request |
| `BluetoothDeviceConnectionResponse`         | 69 | S→C       | Connection result                 |
| `BluetoothGATTGetServicesRequest`           | 70 | C→S       | Get GATT services                 |
| `BluetoothGATTGetServicesResponse`          | 71 | S→C       | Service list                      |
| `BluetoothGATTReadRequest`                  | 72 | C→S       | Read characteristic               |
| `BluetoothGATTReadResponse`                 | 73 | S→C       | Read result                       |
| `BluetoothGATTWriteRequest`                 | 74 | C→S       | Write characteristic              |
| `BluetoothGATTWriteResponse`                | 75 | S→C       | Write result                      |
| `SubscribeBluetoothConnectionsFreeRequest`  | 84 | C→S       | Subscribe to connection count     |
| `BluetoothConnectionsFreeResponse`          | 85 | S→C       | Free slots                        |
| `BluetoothScannerSetModeRequest`            | 92 | C→S       | Set BLE scanner mode              |

### 6.11 Voice Assistant

| Message                               | ID | Direction | Description                               |
|---------------------------------------|----|-----------|-------------------------------------------|
| `SubscribeVoiceAssistantRequest`      | 86 | C→S       | Subscribe to voice pipeline events        |
| `VoiceAssistantResponse`              | 87 | S→C       | Voice assistant events (audio, TTS, etc.) |
| `VoiceAssistantConfigurationRequest`  | 88 | C→S       | Request current config                    |
| `VoiceAssistantConfigurationResponse` | 89 | S→C       | Current configuration                     |
| `VoiceAssistantSetConfiguration`      | 90 | C→S       | Update configuration                      |
| `VoiceAssistantEventDebug`            | 91 | S→C       | Debug events                              |

**Voice Assistant Events (VoiceAssistantResponse):**
- `type`: enum (e.g., `START`, `STT_START`, `STT_END`, `TTS_START`, `TTS_END`, `END`)
- `data`: bytes (audio data for voice messages)
- `conversation_id`: string

### 6.12 Alarm Control Panel

| Message                                 | ID  | Direction | Description           |
|-----------------------------------------|-----|-----------|-----------------------|
| `ListEntitiesAlarmControlPanelResponse` | 126 | S→C       | Alarm entity metadata |
| `AlarmControlPanelStateResponse`        | 127 | S→C       | Current alarm state   |
| `AlarmControlPanelCommandRequest`       | 128 | C→S       | Arm, disarm, etc.     |

### 6.13 Z-Wave Proxy

| Message             | ID | Direction | Description              |
|---------------------|----|-----------|--------------------------|
| `ZWaveProxyFrame`   | 94 | Both      | Z-Wave frame passthrough |
| `ZWaveProxyRequest` | 95 | Both      | Z-Wave network requests  |

### 6.14 Infrared / RF

| Message                               | ID | Direction | Description                |
|---------------------------------------|----|-----------|----------------------------|
| `InfraredRFTransmitRawTimingsRequest` | 96 | C→S       | Transmit IR/RF raw timings |
| `InfraredRFReceiveRequest`            | 97 | C→S       | Start IR/RF receive        |
| `InfraredRFReceiveResponse`           | 98 | S→C       | Received timings           |

### 6.15 Update Entity

| Message                      | ID  | Direction | Description                  |
|------------------------------|-----|-----------|------------------------------|
| `ListEntitiesUpdateResponse` | 128 | S→C       | Update entity metadata       |
| `UpdateStateResponse`        | 129 | S→C       | Update state                 |
| `UpdateCommandRequest`       | 140 | C→S       | Trigger update check/install |

### 6.16 Valve Entity

| Message                     | ID  | Direction | Description           |
|-----------------------------|-----|-----------|-----------------------|
| `ListEntitiesValveResponse` | 141 | S→C       | Valve entity metadata |
| `ValveStateResponse`        | 142 | S→C       | Valve position/state  |
| `ValveCommandRequest`       | 143 | C→S       | Control valve         |

### 6.17 Water Heater

| Message                           | ID  | Direction | Description            |
|-----------------------------------|-----|-----------|------------------------|
| `ListEntitiesWaterHeaterResponse` | 132 | S→C       | Water heater entity    |
| `WaterHeaterStateResponse`        | 133 | S→C       | Current temp, mode     |
| `WaterHeaterCommandRequest`       | 134 | C→S       | Set temperature, mode  |

### 6.18 Date / Time Entities

| Message                        | ID  | Direction | Description      |
|--------------------------------|-----|-----------|------------------|
| `ListEntitiesDateResponse`     | 135 | S→C       | Date entity      |
| `DateStateResponse`            | 136 | S→C       | Current date     |
| `DateCommandRequest`           | 137 | C→S       | Set date         |
| `ListEntitiesTimeResponse`     | 138 | S→C       | Time entity      |
| `TimeStateResponse`            | 139 | S→C       | Current time     |
| `TimeCommandRequest`           | 144 | C→S       | Set time         |
| `ListEntitiesDateTimeResponse` | 145 | S→C       | DateTime entity  |
| `DateTimeStateResponse`        | 146 | S→C       | Current datetime |
| `DateTimeCommandRequest`       | 147 | C→S       | Set datetime     |

---

## 7. Entity Model Patterns

Each entity type follows a consistent three-phase interaction pattern:

### 7.1 Phase 1: List (Discovery)

Client sends `ListEntitiesRequest` → Server streams `ListEntities*Response` messages → Server sends `ListEntitiesDoneResponse`.

Each `ListEntities*Response` includes:
- `object_id`: Unique identifier
- `key`: Numeric key for state/command routing
- `name`: Human-readable name
- `device_class`: Optional classification
- `entity_category`: `NONE=0`, `CONFIG=1`, `DIAGNOSTIC=2`
- `disabled_by_default`: Whether entity is hidden by default

### 7.2 Phase 2: Subscribe States

Client sends `SubscribeStatesRequest` → Server pushes `*StateResponse` messages whenever state changes.

State messages include:
- `key`: Matches the key from list response
- Entity-specific state fields (e.g., `state`, `position`, `brightness`)
- `missing_state`: Boolean indicating if state is unavailable

### 7.3 Phase 3: Command

Client sends `*CommandRequest` → Server processes → Optionally sends updated state.

Command messages include:
- `key`: Target entity
- Command-specific fields (e.g., `state`, `position`, `volume`)
- Optional `device_id` for multi-device entities

---

## 8. Voice Assistant Subsystem

The Voice Assistant messages provide a bidirectional audio streaming pipeline:

### 8.1 Subscription

Client (Home Assistant) sends `SubscribeVoiceAssistantRequest` to receive voice events.

### 8.2 Events (Server → Client)

`VoiceAssistantResponse` (ID 87) carries:

| Field             | Type   | Description                              |
|-------------------|--------|------------------------------------------|
| `type`            | enum   | Event type                               |
| `data`            | bytes  | Audio data or additional payload         |
| `conversation_id` | string | Conversation context                     |
| `error`           | string | Error message if applicable              |

**Event Types:**
- `START`: Voice pipeline started
- `STT_START`: Speech-to-text started
- `STT_END`: Speech-to-text completed; `data` contains transcription
- `TTS_START`: Text-to-speech started
- `TTS_END`: Text-to-speech completed
- `END`: Pipeline finished
- `ERROR`: Error occurred

### 8.3 Configuration

| Message                               | ID | Description          |
|---------------------------------------|----|----------------------|
| `VoiceAssistantConfigurationRequest`  | 88 | Query current config |
| `VoiceAssistantConfigurationResponse` | 89 | Current settings     |
| `VoiceAssistantSetConfiguration`      | 90 | Update settings      |

### 8.4 Audio Streaming

Audio is transmitted as binary data in the `data` field of `VoiceAssistantResponse`. The encoding depends on the device configuration (typically raw PCM or encoded formats).

---

## 9. Protocol Options and Message ID Mapping

### 9.1 Message ID Assignment

Message IDs are assigned in `api.proto` using the `(id)` option:

```proto
message HelloRequest {
  option (id) = 1;
  option (source) = SOURCE_CLIENT;
  ...
}
```

### 9.2 Source Type

Defined in `api_options.proto`:

| Type            | Value | Meaning                            |
|-----------------|-------|------------------------------------|
| `SOURCE_BOTH`   | 0     | Message can be sent by either side |
| `SOURCE_SERVER` | 1     | Server-initiated only              |
| `SOURCE_CLIENT` | 2     | Client-initiated only              |

### 9.3 Other Options

| Option                   | Purpose                                      |
|--------------------------|----------------------------------------------|
| `needs_setup_connection` | Whether message requires completed handshake |
| `needs_authentication`   | Whether message requires authentication      |
| `no_delay`               | Send immediately without Nagle delay         |
| `ifdef`                  | Conditional compilation (server-side)        |
| `log`                    | Whether to log this message type             |

---

## 10. Extensibility and Compatibility

### 10.1 API Versioning

The `api_version_major` and `api_version_minor` in `HelloRequest`/`HelloResponse` govern compatibility:

- **Major version mismatch:** Immediate disconnect (breaking protocol change)
- **Minor version mismatch:** Warning logged; client should handle unknown message types gracefully

### 10.2 Adding New Messages

New messages are added to `api.proto` with:
1. Unique `(id)` number (not colliding with existing IDs)
2. Appropriate `source` (CLIENT, SERVER, or BOTH)
3. Optional `needs_authentication` / `needs_setup_connection` flags
4. Conditional compilation with `ifdef` if feature-dependent

### 10.3 Backward Compatibility Strategy

- Never reuse or reassign deleted message IDs
- Mark deprecated fields with `option deprecated = true`
- Add new optional fields rather than removing/renaming existing ones
- Clients must ignore unknown message types (future-proof)

### 10.4 Deprecated Messages

| Deprecated                               | Notes                                             |
|------------------------------------------|---------------------------------------------------|
| `AuthenticationRequest` (ID 3)           | Removed in 2026.1.0; replaced by Noise encryption |
| `AuthenticationResponse` (ID 4)          | Same as above                                     |
| `LegacyCoverState`, `LegacyCoverCommand` | Deprecated in API v1.1                            |
| `LegacyBluetoothLEAdvertisementResponse` | Removed in 2025.8.0                               |

---

## 11. Troubleshooting and Interoperability Notes

### 11.1 Common Issues

| Issue                  | Likely Cause                       | Resolution                                            |
|------------------------|------------------------------------|-------------------------------------------------------|
| Connection refused     | Port 6053 not listening            | Verify `api:` component is configured; check firewall |
| Authentication failed  | Missing/wrong encryption key       | Ensure `encryption.key` matches on both sides         |
| Version mismatch       | HA and ESPHome API versions differ | Update HA or ESPHome to compatible versions           |
| Entity missing         | Entity not defined in YAML         | Check ESPHome config; restart ESPHome                 |
| State not updating     | `SubscribeStatesRequest` not sent  | Ensure client subscribes after Hello                  |
| Audio pipeline stalled | Voice assistant not enabled        | Verify `voice_assistant:` component in config         |

### 11.2 Diagnostic Commands

On the ESPHome device (via UART/SSH):
- `api` command: Show API connection status
- `logger` level tuning: Increase verbosity to debug connection issues

### 11.3 Wire Sharking the Protocol

To dissect in Wireshark:
1. Filter: `tcp.port == 6053`
2. Decode as Protobuf (custom `api.proto`)

---

## Appendix A: Message ID Ranges (Summary)

| Range   | Category                           |
|---------|------------------------------------|
| 1-10    | Connection & Hello                 |
| 11-19   | Entity list (discovery)            |
| 20-29   | States, logs                       |
| 30-40   | Commands, HA integration           |
| 41-50   | Services, cameras, climate         |
| 51-70   | Entity commands, BT                |
| 71-90   | BT, voice assistant                |
| 91-100  | BT, Z-Wave, IR                     |
| 124-125 | Encryption                         |
| 126-130 | Alarm, update                      |
| 131-140 | Responses, water heater, date/time |
| 141-147 | Valve, datetime                    |

> **Note:** This is a high-level summary. The authoritative mapping is in `api.proto`.

---

## Appendix B: Example Sequences

### B.1 Hello + List Entities + Subscribe States

```
Client → Server: HelloRequest (1) {client_info: "Home Assistant", api_version_major: 1, api_version_minor: 9}
Server → Client: HelloResponse (2) {api_version_major: 1, api_version_minor: 9, name: "esp32-node"}
Client → Server: NoiseEncryptionSetKeyRequest (124) {key: <32-byte-key>}
Server → Client: NoiseEncryptionSetKeyResponse (125) {success: true}
Client → Server: DeviceInfoRequest (9)
Server → Client: DeviceInfoResponse (10) {name: "esp32-node", mac_address: "...", esphome_version: "2026.1.0"}
Client → Server: ListEntitiesRequest (11)
Server → Client: ListEntitiesLightResponse (15) {key: 1, name: "Living Room Light", ...}
Server → Client: ListEntitiesSensorResponse (16) {key: 2, name: "Temperature", ...}
Server → Client: ListEntitiesDoneResponse (19)
Client → Server: SubscribeStatesRequest (20)
Server → Client: LightStateResponse (24) {key: 1, state: true, brightness: 0.8}
Server → Client: SensorStateResponse (25) {key: 2, state: 22.5}
```

### B.2 Voice Assistant Audio Pipeline

```
Client → Server: SubscribeVoiceAssistantRequest (86)
Server → Client: VoiceAssistantResponse (87) {type: START}
Client → Server: (audio data stream via separate channel or embedded)
Server → Client: VoiceAssistantResponse (87) {type: STT_END, data: "turn on the lights"}
Server → Client: VoiceAssistantResponse (87) {type: TTS_START}
Server → Client: VoiceAssistantResponse (87) {type: TTS_END, data: <audio-bytes>}
Server → Client: VoiceAssistantResponse (87) {type: END}
```

---

## Appendix C: Related Documentation

- [ESPHome API Component](https://esphome.io/components/api.html)
- [ESPHome Voice Assistant](https://esphome.io/components/voice_assistant.html)
- [ESPHome Bluetooth Proxy](https://esphome.io/components/bluetooth_proxy.html)
- [aioesphomeapi (Python client library)](https://github.com/esphome/aioesphomeapi)
- [Protocol Buffers Language Guide](https://developers.google.com/protocol-buffers/docs/proto3)
