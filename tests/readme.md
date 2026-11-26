# Automated testing

From the root of LVA run:-
```./script/tests```


# End-to-end tests

From the root of LVA run:-
```./script/e2e```

Starts a real LVA instance with a virtual microphone and speaker and a fake Home Assistant, see `tests/e2e/README.md`. It runs in CI as `.github/workflows/e2e-tests.yml`.


# Manual testing

Most of what used to be tested by hand is covered by the end-to-end tests (`tests/e2e`). Only real hardware and real Home Assistant behavior remain manual.

| Scenario | Covered by |
|----------|------------|
| Announcement (`assist_satellite.announce`) | `test_home_assistant_interface.py::test_announcement_is_played_and_confirmed` |
| TTS (`tts.speak`) | `test_media_player.py::test_text_to_speech_over_music_pauses_and_resumes_the_music` |
| Music: start and stop | `test_media_player.py::test_media_player_plays_and_stops_music` |
| Music: next track | `test_media_player.py::test_next_track_replaces_the_current_one` |
| Music: pause and resume | `test_media_player.py::test_media_player_pauses_and_resumes_music` |
| Timer with music, duck and unduck, stopped by stop word | `test_timer.py::test_ringing_timer_ducks_music_and_stop_word_restores_it` |
| Timer stopped by wake word | `test_timer.py::test_wake_word_silences_ringing_timer_and_starts_pipeline` |

Not automated: `--mpv-player-duck-mode pause`, because it needs a second LVA instance with its own microphone, and everything that depends on a real sound card, microphone or Home Assistant instance.
