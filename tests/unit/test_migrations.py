"""Tests for the one-time migrations of stored data."""

from pathlib import Path

from linux_voice_assistant.migrations import Migration591, Migration644, migrate_preferences
from linux_voice_assistant.models import AvailableWakeWord, Preferences, WakeWordSelection, WakeWordType

MICRO_ID = "Internal - MWW - Hey Jarvis"
OPEN_ID = "Internal - OWW - Hey Jarvis"
MICRO = WakeWordSelection("internal", "mww", "hey_jarvis")
OPEN = WakeWordSelection("internal", "oww", "hey_jarvis_v0.1")


def _available() -> dict:
    return {
        MICRO_ID: AvailableWakeWord(MICRO_ID, WakeWordType.MICRO_WAKE_WORD, "Hey Jarvis", ["en"], Path("hey_jarvis.json"), origin="internal", file_id="hey_jarvis"),
        OPEN_ID: AvailableWakeWord(OPEN_ID, WakeWordType.OPEN_WAKE_WORD, "Hey Jarvis", ["en"], Path("hey_jarvis_v0.1.json"), origin="internal", file_id="hey_jarvis_v0.1"),
    }


def _migrate(stored, applied=None) -> Preferences:
    return migrate_preferences(Preferences(active_wake_words=stored, applied_migrations=applied or []), _available())


def test_former_id_becomes_selection():
    assert _migrate(["hey_jarvis", "hey_jarvis_v0.1"]).active_wake_words == [MICRO, OPEN]


def test_plain_phrase_becomes_microwakeword_selection():
    assert _migrate(["Hey Jarvis"]).active_wake_words == [MICRO]


def test_former_openwakeword_name_becomes_openwakeword_selection():
    assert _migrate(["Hey Jarvis (OWW)"]).active_wake_words == [OPEN]


def test_selection_name_becomes_selection():
    assert _migrate([OPEN_ID, None]).active_wake_words == [OPEN, None]


def test_names_are_matched_case_insensitive():
    assert _migrate(["hey jarvis"]).active_wake_words == [MICRO]


def test_existing_selections_and_unknown_values_are_kept(caplog):
    with caplog.at_level("DEBUG"):
        migrated = _migrate([OPEN, "Nope"])
    assert migrated.active_wake_words == [OPEN, "Nope"]
    assert "Stored wake word 'Nope' is neither an id nor a known name" in caplog.text


def test_logs_each_replaced_value(caplog):
    with caplog.at_level("INFO"):
        _migrate(["Hey Jarvis"])
    assert f"Stored wake word 'Hey Jarvis' is now stored as '{MICRO_ID}'" in caplog.text


def test_marks_the_migration_as_applied():
    assert _migrate([MICRO], applied=[1]).applied_migrations == [1, Migration591.id, Migration644.id]


def test_applied_migration_does_not_run_again():
    preferences = Preferences(active_wake_words=["Hey Jarvis"], applied_migrations=[Migration591.id, Migration644.id])
    assert migrate_preferences(preferences, _available()) is preferences


def test_does_not_change_the_given_preferences():
    preferences = Preferences(active_wake_words=["Hey Jarvis"])
    migrate_preferences(preferences, _available())
    assert preferences.active_wake_words == ["Hey Jarvis"]
    assert preferences.applied_migrations == []


def test_migration_591_changes_only_the_wake_words():
    preferences = Preferences(active_wake_words=["Hey Jarvis"], volume=0.5)
    migrated = Migration591.migrate(preferences, _available())
    assert migrated.active_wake_words == [MICRO]
    assert migrated.volume == 0.5
    assert migrated.applied_migrations == []


def test_preferences_file_holds_origin_engine_and_file_name(tmp_path):
    import json

    preferences = Preferences(active_wake_words=[MICRO, None])
    path = tmp_path / "preferences.json"
    preferences.save(path)

    assert json.loads(path.read_text())["active_wake_words"] == [{"origin": "internal", "engine": "mww", "file_id": "hey_jarvis"}, None]
    assert Preferences.from_dict(json.loads(path.read_text())).active_wake_words == [MICRO, None]


def test_migration_644_marks_changed_names_when_wake_words_are_stored(caplog):
    with caplog.at_level("INFO"):
        migrated = Migration644.migrate(Preferences(active_wake_words=[MICRO, None]), _available())
    assert migrated.wake_word_names_changed is True
    assert "The names of the wake words in Home Assistant changed" in caplog.text


def test_migration_644_leaves_a_new_installation_alone():
    preferences = Preferences(active_wake_words=[None, None])
    assert Migration644.migrate(preferences, _available()) is preferences
    assert Migration644.migrate(Preferences(), _available()).wake_word_names_changed is False
