### Entities / HA-exposed controls

- **Wake word library select entity** (`entity.py`, `util.py`, `satellite.py`): `WakeWordLibrarySelectEntity` now dynamically discovers available libraries from subdirectories under `wakewords/` that contain at least one valid model JSON. Options are derived automatically from directory names (no normalization). Changing the selection triggers a deferred reconnect to refresh HA's wake word dropdown options.

- **Wake word sensitivity select entity** (`entity.py`): New `WakeWordSensitivitySelectEntity` exposes a Select entity for sensitivity levels: "Model default", "Slightly sensitive", "Moderately sensitive", "Very sensitive". Applies probability cutoffs immediately on change:
  - openWakeWord: 0.5 (very), 0.7 (moderate), 0.9 (slight)
  - microWakeWord: Uses per-model defaults when "Model default" is selected; otherwise overrides with explicit cutoffs.

### Wake word model handling

- **Legacy model ID mapping** (`util.py`): Added `map_legacy_model_id()` function and `_LEGACY_MODEL_IDS` dict to map legacy model IDs (e.g., `hey_jarvis_v0.1`) to canonical IDs (e.g., `hey_jarvis`). Provides backward compatibility for preferences and CLI args.

- **Wakewords directory refactor** (`util.py`): Added `scan_wake_words_for_library()` to scan wake word models by library type (`microWakeWord` or `openWakeWord`) from the appropriate subdirectories.

- **Ordered wake word list preservation** (`satellite.py`): `VoiceAssistantSetConfiguration` handler now preserves the ordered list from HA's `msg.active_wake_words` instead of converting to a set, maintaining slot 0/slot 1 ordering. Includes legacy ID mapping during processing.

- **Wake word 2 limitation warning** (`satellite.py`): Logs a warning when HA sends a 1-item active_wake_words list, documenting the inherent limitation where the ESPHome API has no slot indices.

### Assist state reset / cancel semantics

- **Mute state persistence** (`satellite.py`, `models.py`, `__main__.py`): Mute state is now persisted to `preferences.json` (`muted` boolean field) and restored on startup. The `_set_muted()` method saves preferences immediately on toggle.

- **Mute/stop cancel behavior** (`satellite.py`): When muted, the assistant now stops any ongoing TTS playback, stops wake word detection, resets the pipeline, and publishes idle/ready state. Unmuting plays an unmute sound and resumes normal operation.

### Preferences schema changes (code-level)

- **Preferences dataclass** (`models.py`): Added fields:
  - `wake_word_library: Optional[str]` - "microWakeWord" or "openWakeWord"
  - `wake_word_sensitivity: Optional[float]` - 0.5, 0.7, 0.9, or None for "Model default"
  - `muted: bool` - persist mute state across restarts

- **ServerState dataclass** (`models.py`): Added fields:
  - `wake_word_library_select_entity: Optional[WakeWordLibrarySelectEntity]`
  - `wake_word_sensitivity_select_entity: Optional[WakeWordSensitivitySelectEntity]`
  - `oww_probability_cutoff: float` - current openWakeWord probability threshold
  - `micro_default_cutoffs: Dict[str, float]` - stored defaults per microWakeWord model
  - `wake_words_changed: bool` - flag to signal wake word list update

### ESPHome API server + message dispatch fixes

- **Keyed dispatch for SelectCommandRequest** (`satellite.py`): `SelectCommandRequest` messages are now routed by key to the matching entity only, rather than broadcasting to all entities.

- **Keyed dispatch for SwitchCommandRequest** (`satellite.py`): `SwitchCommandRequest` messages are now routed by key to the matching entity only, fixing incorrect dispatch to multiple entities.

- **API server handshake stability** (`api_server.py`): Added print statement at connection start (`connection_made`) for debugging; cleaned up logging to reduce noise while maintaining observability.

### Logging cleanup (per spec)

- Applied logging specification cleanup across `satellite.py`, `api_server.py`, `entity.py`, and `models.py` to reduce log spam while preserving essential debugging information.
