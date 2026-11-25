# Automated testing

From the root of LVA run:-
```./script/tests```


# End-to-end tests

From the root of LVA run:-
```./script/e2e```

Starts a real LVA instance with a virtual microphone and speaker and a fake Home Assistant, see `tests/e2e/README.md`. It runs in CI as `.github/workflows/e2e.yml`.


# Manual testing

## Functions from HA side:

### Announcement:

Function in HA is: assist_satellite.announce

### TTS:

Function in HA is: tts.speak

## Play music from MA:

### Start and stop
Start playing a playlist. Check if it is playing. Stop playing. Check if it is stopped.

### Next track

Start playing a playlist. Check if it is playing. Next track. Check if it is playing.

### Pause

## Timer

Play music and then start a timer. Test if duck and unduck works. Test if unduck work when using wake word or stop word to stop the timer ringing.

