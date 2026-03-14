# Wake word 2 slot limitation (Home Assistant / ESPHome Voice Assistant config)

## Issue Description

When using Home Assistant's built-in ESPHome Voice Assistant configuration UI to set wake words, there is a fundamental limitation in how the wake word configuration is communicated to LVA (linux-voice-assistant).

## Root Cause

The ESPHome Voice Assistant API message `VoiceAssistantSetConfiguration.active_wake_words` is a **simple ordered list of model IDs** with **no slot indices**. This means:

- The list cannot represent "holes" in the slot configuration
- There is no way to say "slot 0 is empty, but slot 1 has a wake word"
- The API always assumes the first wake word in the list maps to slot 0, and the second (if present) maps to slot 1

## Examples

| Configuration | HA sends to LVA |
|--------------|-----------------|
| Wake word = "hey_jarvis", Wake word 2 = "alexa" | `["hey_jarvis", "alexa"]` |
| Wake word = "No wake word", Wake word 2 = "alexa" | `["alexa"]` (cannot represent hole!) |
| Wake word = "No wake word", Wake word 2 = "No wake word" | `[]` |
| Wake word = "hey_jarvis", Wake word 2 = "No wake word" | `["hey_jarvis"]` |

## The Problem

If you want Wake word (slot 0) to be "No wake word" while Wake word 2 (slot 1) is set to a model like "alexa", **this is impossible** using HA's built-in UI. The API simply sends a 1-item list `["alexa"]`, and LVA has no way to know you intended it for slot 1.

LVA interprets a 1-item list as Wake word (slot 0) because there is no other information available.

## This Is Not an LVA Bug

This limitation is inherent to the HA/ESPHome Voice Assistant configuration encoding. LVA correctly interprets the data as sent by Home Assistant. The warning log added by LVA helps diagnose this situation:

```
WARNING - HA sent 1-item active_wake_words list: ['alexa']. HA/ESPHome API has no slot indices - cannot set Wake word 2 (slot 1) without Wake word (slot 0). LVA interprets 1-item list as Wake word (slot 0).
```

## Recommended User Workflow

1. **Set Wake word (slot 0) first** - Always ensure the primary wake word is configured before setting Wake word 2
2. **You cannot have Wake word = "No wake word" while Wake word 2 is set** when using HA's built-in UI
3. If you need the second slot without the first, you would need:
   - A custom HA dashboard/frontend that can send the raw protobuf message with explicit slot indices (not currently supported by standard ESPHome API)
   - Direct configuration via LVA's preferences file (`preferences.json`)

## References

- LVA source: `linux_voice_assistant/satellite.py` - `VoiceAssistantSetConfiguration` handler
- ESPHome Voice Assistant API documentation
- Home Assistant Assist voice assistant configuration
