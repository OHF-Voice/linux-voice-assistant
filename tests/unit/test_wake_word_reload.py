"""Tests for linux_voice_assistant/wake_word_reload.py."""

import logging

from linux_voice_assistant.wake_word_reload import HandshakeAction, WakeWordListReload, handshake_action, reported_active_wake_words


class TestHandshakeAction:
    def test_nothing_to_do_without_a_rename(self):
        assert handshake_action(WakeWordListReload.NONE) == HandshakeAction(next_step=WakeWordListReload.NONE, reconnect=False, report_first_only=False)

    def test_changed_names_reconnect_once_more(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.wake_word_reload"):
            action = handshake_action(WakeWordListReload.NAMES_CHANGED)

        assert action == HandshakeAction(next_step=WakeWordListReload.RESTORE_SELECTION, reconnect=True, report_first_only=False)
        assert "Wake word names changed: Home Assistant now has the new list, reconnecting once more" in caplog.text

    def test_restore_reports_the_first_wake_word_only_and_ends(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="linux_voice_assistant.wake_word_reload"):
            action = handshake_action(WakeWordListReload.RESTORE_SELECTION)

        assert action == HandshakeAction(next_step=WakeWordListReload.NONE, reconnect=False, report_first_only=True)
        assert "reporting only the first active wake word" in caplog.text


class TestReportedActiveWakeWords:
    def test_active_wake_words_in_slot_order(self):
        assert reported_active_wake_words(["b", "a"], {"a", "b"}, report_first_only=False) == ["b", "a"]

    def test_inactive_and_empty_slots_are_left_out(self):
        assert reported_active_wake_words([None, "a", "gone"], {"a"}, report_first_only=False) == ["a"]

    def test_first_only_drops_the_second_and_warns(self, caplog):
        with caplog.at_level(logging.WARNING, logger="linux_voice_assistant.wake_word_reload"):
            reported = reported_active_wake_words(["a", "b"], {"a", "b"}, report_first_only=True)

        assert reported == ["a"]
        assert "Wake word 'b' is still active, but Home Assistant shows 'No wake word' for it after the rename. Choose it again in Home Assistant." in caplog.text

    def test_first_only_with_only_slot_2_reports_it(self, caplog):
        with caplog.at_level(logging.WARNING, logger="linux_voice_assistant.wake_word_reload"):
            assert reported_active_wake_words([None, "b"], {"b"}, report_first_only=True) == ["b"]

        assert not caplog.text
