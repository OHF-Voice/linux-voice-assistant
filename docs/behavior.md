# Behavior Guide

This page describes what Linux Voice Assistant (LVA) does in everyday situations, from the user's point of view: what happens to music when you talk to the assistant, what happens when a timer rings, what the stop word does, and so on.

For the list of Home Assistant entities see [configuration.md](configuration.md). For the events sent to hardware peripherals see [peripheral_api.md](peripheral_api.md).

---

## Contents

- [Basics: two audio channels](#basics-two-audio-channels)
- [Talking to the assistant](#talking-to-the-assistant)
- [Music and media playback](#music-and-media-playback)
- [Announcements](#announcements)
- [Timers](#timers)
- [The stop word](#the-stop-word)
- [Mute](#mute)
- [Volume](#volume)
- [Buttons (peripherals)](#buttons-peripherals)
- [Connection to Home Assistant](#connection-to-home-assistant)
- [Quick reference: what happens to music?](#quick-reference-what-happens-to-music)

---

## Basics: two audio channels

LVA has two independent audio players that share one volume setting:

| Player | Used for |
|--------|----------|
| **Music player** | Media sent from Home Assistant (music, radio, `media_player.play_media` without `announce`) |
| **Assistant player** | Wake / mute / unmute chimes, the thinking sound, spoken replies (TTS), announcements, timer ringing, button sounds |

The music player can be sent to a different sound card with `--music-output-device`. By default both use `--audio-output-device`. `--audio-output-device` defaults to `pulse`, the default PulseAudio output (PulseAudio itself, or PipeWire through `pipewire-pulse`). A device name carries its mpv driver as a prefix (for example `pulse/...`) and that driver is used.

Two rules apply everywhere:

- **Music is never ended by the assistant on its own.** During a conversation it is lowered (*ducked*). During a TTS / announcement sent to the media player (for example `tts.speak` with `announce: true`) it is **paused** for the duration of the announcement and then resumed. Only an explicit *stop* from Home Assistant or a peripheral, or losing the connection to Home Assistant (see below), stops it for good.
- **Ducking** means the music volume is reduced to **50 %** of the volume you set. When the interaction is over it returns to 100 % of your set volume.

---

## Talking to the assistant

A normal voice interaction runs through these phases:

| # | Phase | What you hear / what happens | Music |
|---|-------|------------------------------|-------|
| 1 | **Wake word** is detected | The wake sound plays (`--wakeup-sound`). | Ducked to 50 % immediately |
| 2 | **Listening** | Your speech is streamed to Home Assistant. By default listening starts when the wake sound has finished playing. With `--listen-during-wake-sound` it starts right away, so you can talk over the chime. | Stays ducked |
| 3 | **Thinking** | Home Assistant processes the request. If the *Thinking Sound* switch is on, a short sound plays. | Stays ducked |
| 4 | **Speaking** | The spoken reply (TTS) plays. The stop word is now active. | Stays ducked |
| 5 | **Done** | The assistant returns to idle. | Restored to full volume after the reply has finished playing |

Details:

- **No wake word during a conversation.** While a conversation is running (phases 1–4, including the delay before a follow-up and the pipeline ended sound) the wake word models are paused and do not listen at all. This prevents the assistant from triggering itself on its own voice and saves CPU. They are reset when the conversation ends, so audio from before the pause is never scored. See the dead zone below.
- **Dead zone after a conversation (about 0.2 s).** When the wake word models resume, they have to refill their sliding windows before they can score anything. For about 0.2 s after a conversation ended, a wake word is therefore not detected. The same applies when the assistant is started. Only a wake word spoken in this short time is affected; everything after that is detected as usual. This is the same behavior as the stop word has after it becomes active (see [The stop word](#the-stop-word)).
- **Refractory period (`--refractory-seconds`, default 0 = off).** Optional extra block. When set above 0, every detection after an accepted wake word, of any wake word, is blocked for this many seconds and discarded. By default it is off, because the wake word models are already paused while a conversation runs, which prevents repeated triggers. It can still help with a very short conversation or a wake word that is detected again right after the pause ends. The wake word models keep running during that time; only their result is dropped. Each blocked detection is logged at debug level (`blocked by refractory period`, with the probability, the threshold and the time left). Setting it to `0` (the default) turns the block off. The period is counted from the accepted detection, not from the end of the conversation, so it matters mostly for very short conversations.
- **Early speech.** If Home Assistant streams the reply (the response starts before it is complete), playback starts as soon as the first part is available.
- **No spoken reply.** If the pipeline finishes without any speech (for example no speech was detected, an error, or a command with no reply), the pipeline ended sound (`--pipeline-ended-sound`) plays. Afterwards the assistant goes back to idle and music is restored. The sound is not played after announcements, when the reply is stopped with the stop word, when another satellite heard the same wake word and Home Assistant gave the run to it (`duplicate_wake_up_detected`), or when Home Assistant ends the run right after speech recognition because something else handles the transcript (for example the answer to a question asked by an automation).
- **Continued conversation.** If Home Assistant asks a follow-up question, the assistant listens again after the reply, without a wake word. It waits `--continue-conversation-delay` (default 0.5 s) after the reply so the microphone does not record the tail of its own voice. Music stays ducked through the whole conversation and is restored only when the conversation ends. The follow-up runs on the same pipeline as the wake word that started the conversation. After a button press or an announcement from Home Assistant there is no wake word, so the first pipeline is used. If you mute the assistant during that delay, the follow-up is skipped and music is restored.
- **Wake word while a timer rings.** Saying the wake word silences the ringing timer and starts a normal interaction.
- **Starting by button.** A peripheral button can start listening without a wake word. Instead of the wake sound, the start-listening sound (`--start-listening-sound`) plays; everything else is identical. This also silences a ringing timer.
- **Errors.** If the pipeline reports an error, peripherals receive a `pipeline_error` event. See [peripheral_api.md](peripheral_api.md).
- **Two wake words.** Home Assistant can activate up to two wake words at once. Each has its own sensitivity setting (*Wake Word 1/2 Sensitivity*). A word keeps its slot while the other one changes, and always uses the sensitivity of its own slot, also when it is the only active word.
- **Wake word locations.** Wake words are read from `wakewords/internal`, `wakewords/downloaded` and `wakewords/custom` (root: `--wake-word-root-dir`), each with a `microwakeword` and an `openwakeword` subdirectory. The same model in two directories is two separate wake words. `--wake-model` and `--wake-model-2` accept the file name (`hey_jarvis`) or the name in the selection (`Internal - MWW - Hey Jarvis`). If a file name exists in more than one origin, `custom` wins over `downloaded`, which wins over `internal`. The same applies to the stop word. Wake words that Home Assistant sends are stored in `downloaded`.
- **Wake word names.** Every wake word has a name made of origin, engine and the phrase from its `.json` file, for example `Download - OWW - Hey Jarvis` (`Internal`, `Download`, `Custom` and `MWW`, `OWW`). It is the name in the wake word selection in Home Assistant and also the phrase LVA sends with a voice request, because Home Assistant compares both to choose the pipeline of a wake word. If two wake words of one origin and engine have the same phrase, only the last one found is used and a warning is logged.
- **Stored wake words.** `preferences.json` stores each active wake word with its origin, engine and file name, for example `{"origin": "internal", "engine": "mww", "file_id": "hey_jarvis"}`. Once after the update that introduced this, wake words stored under a former id (`hey_jarvis`) or a name (`Hey Jarvis`, `Hey Jarvis (OWW)` or `Internal - MWW - Hey Jarvis`) are replaced by such an entry and a message is logged. A plain phrase means the microWakeWord model. Values that match nothing are kept and then skipped at startup with a warning. A former id that Home Assistant still sends is resolved to the wake word as well. The numbers of the migrations that ran are stored in `applied_migrations`.
- **Default wake words.** `--wake-model` fills the first slot and `--wake-model-2` the second one, but only while no wake words are stored yet (first start, or after the preferences file was removed). Once the active wake words are stored, the choice made in Home Assistant is kept and both options are ignored. Without `--wake-model-2` (the default), only one wake word is active. If it is unknown, fails to load or is the same as the first wake word, it is skipped with a log message and the assistant starts with one wake word.

---

## Music and media playback

Music started from Home Assistant (a media player service call or the media player card) plays on the music player.

| Action | What happens |
|--------|--------------|
| Play | The track plays. Home Assistant sees the state `playing`. |
| Pause / Resume | The music pauses or resumes. |
| Stop | The music stops and the state becomes `idle`. |
| Track ends | The state becomes `idle` and Home Assistant is informed. |
| Volume change | Applies to music **and** assistant sounds, see [Volume](#volume). |
| Wake word / conversation | Music is ducked to 50 % and restored afterwards, see [Talking to the assistant](#talking-to-the-assistant). |
| Timer rings | Music is ducked while the timer rings and restored when it stops, see [Timers](#timers). |
| Mute switch | Does **not** affect music, see [Mute](#mute). |

If music is paused when you start talking to the assistant, it stays paused. Ducking only changes the volume. The music is not resumed automatically at the end of a conversation.

---

## Announcements

There are two kinds of announcements, and they treat running music differently.

### Voice announcements (Assist satellite announce)

Triggered by Home Assistant's Assist satellite, for example `assist_satellite.announce` or "start conversation" with a prompt.

1. The music is **ducked to 50 %**.
2. An optional pre-announce sound plays, followed by the spoken announcement.
3. The stop word is active while it plays, so you can say "stop" to cut it short.
4. When the announcement ends the music is restored to full volume.

If the announcement was started with `start_conversation`, the assistant listens for your answer afterwards, exactly like a continued conversation (the music stays ducked until that conversation ends).

### Media player announcements and TTS (`announce: true`)

Triggered by a `media_player.play_media` call with `announce: true` on the LVA media player, which is also what `tts.speak` with `announce: true` does. Here the music is **paused** while the announcement is spoken, not just lowered.

| Music state when the announcement arrives | What happens |
|-------------------------------------------|--------------|
| **Playing** | The music is **paused** (not just lowered), the announcement plays, then the music **resumes** where it stopped. |
| **Paused** (by you) | The announcement plays, the music stays paused and the media player reports `paused` afterwards. |
| **Not playing** | The announcement plays, the media player returns to `idle`. |

---

## Timers

Timers are created by voice ("set a timer for 5 minutes") and managed by Home Assistant. LVA shows and rings them.

| Event | What happens |
|-------|--------------|
| Timer started / updated | Peripherals receive `timer_ticking` / `timer_updated` with the remaining time (for example to show a countdown on LEDs). |
| Timer cancelled | The countdown is cleared on peripherals (`timer_updated` with 0 seconds left) and the assistant returns to idle. |
| **Timer finished** | The music is **ducked to 50 %**, the stop word becomes active and the timer sound (`--timer-finished-sound`) plays on repeat with a 1 second pause between repetitions. |
| Timer is ringing and you say the **stop word** | The ringing stops, music is restored, the assistant goes idle. |
| Timer is ringing and you say the **wake word** or press the **start-listening button** | The ringing stops and a normal interaction begins. |
| Timer is ringing and a peripheral sends `stop_timer_ringing` or `stop_pipeline` | The ringing stops, music is restored, the assistant goes idle. |
| Nobody reacts | The timer **stops ringing automatically** after `--timer-max-ring-seconds` (default 900 s = 15 minutes) and the music is restored. |

Notes:

- A timer finishing does not stop or pause the music. It is only ducked, so a ringing timer is audible over it.
- While the assistant is muted, the stop word and the wake word do not work. Unmute first, or stop the ringing with a peripheral button.

---

## The stop word

The stop word (default model `stop`, configurable with `--stop-model`) is a safety word that is **only listened for when something is audible that you might want to cancel**:

- while a spoken reply is playing,
- while an announcement is playing,
- while the thinking sound is playing,
- while a timer is ringing.

At all other times it is ignored, so saying "stop" in a normal conversation does nothing. This avoids accidental triggers.

The stop model is reset each time it becomes active again, so a word said in the
first fraction of a second (about 0.2 s) of a reply, announcement or ringing timer
may not be heard. Anything after that is detected as usual.

What it does:

| Situation | Effect |
|-----------|--------|
| Timer ringing | Timer silenced, music restored, idle. |
| Spoken reply playing | Reply is cut off. The assistant then finishes as if the reply had ended: music is restored and it returns to idle. If the reply had asked a follow-up question, the assistant still starts listening for the answer. |
| Announcement playing | Announcement is cut off, music is restored. |

The stop word does **not** stop music that you started from Home Assistant. Use the media player (or a peripheral button) for that.

The detection sensitivity can be adjusted with the *Stop Word Sensitivity* number in Home Assistant.

---

## Mute

The **Mute** switch (Home Assistant device page) turns the assistant's ears off.

When muted:

- The wake word and the stop word are disabled, and a button cannot start listening.
- No microphone audio is sent to Home Assistant.
- A running spoken reply or announcement is stopped and the mute sound (`--mute-sound`) plays.
- **Music keeps playing.** Mute concerns the voice assistant only.
- Peripherals receive a `muted` event (for example to show a red LED).

When unmuted, the unmute sound (`--unmute-sound`) plays and the assistant returns to idle.

Do not confuse this with the **media player mute**, which sets the shared volume to 0, see below.

---

## Volume

- There is **one shared volume** for music and for all assistant sounds (chimes, replies, timer). Changing it on the media player entity or with a peripheral's volume buttons changes both.
- The volume is saved in `preferences.json` and restored after a restart.
- **First start.** If the preferences file does not exist yet, it is created right at startup with the defaults and an info message is logged, instead of on the first change. Startup may write the file again, for example when the default wake words are stored or when `--enable-thinking-sound`, `--mic-volume`, `--mic-auto-gain` or `--mic-noise-suppression` is set.
- **Media player mute** (the speaker icon on the media player card) sets the volume to 0 and remembers the previous value. Unmuting restores it.
- **Ducking** is temporary and independent of the set volume: it always means 50 % of the current volume.
- A separate **Mic Volume** controls the microphone level. It does not affect playback, see [configuration.md](configuration.md). It is saved in `preferences.json` and restored after a restart. Only an explicitly given `--mic-volume` (0 to 100) overrides the stored value on start. Without it, the stored value is used, or 100 if none is stored yet.

---

## Microphone failure

If the audio server reports that the microphone stream failed (for example after the device was suspended, unplugged, or PipeWire/PulseAudio restarted), the assistant logs an error naming the microphone and immediately tries to reopen it. If that fails, it waits 2 seconds and tries once more. After a successful reconnect, listening continues without a restart.

If the second attempt also fails, the assistant logs the error and exits with a non-zero status, so a supervisor such as Docker (`restart: unless-stopped`) or systemd (`Restart=on-failure`) restarts it. It never keeps running without a working microphone.

---

## Buttons (peripherals)

If a peripheral (such as a HAT with buttons) is connected through the [peripheral API](peripheral_api.md), it can trigger the following. What a physical button does is defined by the peripheral, not by LVA.

| Action | Result |
|--------|--------|
| Start listening | Plays the start-listening sound and begins an interaction without a wake word. Also silences a ringing timer. Ignored while muted. |
| Stop pipeline | Stops the current reply or a ringing timer, same as the stop word. |
| Mute / unmute microphone | Same as the Mute switch. |
| Volume up / down / set | Changes the shared volume by `--peripheral-volume-step`. |
| Stop / pause / resume media | Controls the music player directly. |
| Single press | Reported to Home Assistant as a `single_press` event (no sound). |
| Double / triple / long press | Plays the matching sound (`--button-double-press-sound`, `--button-triple-press-sound`, `--button-long-press-sound`) and reports the event to Home Assistant so you can use it in automations. |

The *Disable button controls* switch (if the peripheral provides it) lets you lock the physical buttons from Home Assistant.

---

## Connection to Home Assistant

- LVA is discovered by Home Assistant via mDNS (ESPHome integration). Several clients can be connected at the same time.
- **Without mDNS.** With `--enable-zeroconf 0`, LVA announces nothing on the network, so Home Assistant does not discover it. Add it manually in the ESPHome integration with the host IP address and port `6053` (`--port`). Everything else (ESPHome API, peripheral API, `zeroconf` peripheral events) works as before.
- **Identity.** Home Assistant knows the device by its MAC address, which is also in its name (`lva-<mac>`): by default the network interface's. With `--mac-address` it is that one instead, so the device stays the same in Home Assistant when the machine uses another interface, and several instances on one machine get one identity each.
- **Encryption.** With `--enable-encryption`, the connection is encrypted as on ESPHome devices with an API encryption key. The key is read from `--encryption-key-file` (default: `encryption.key` next to the preferences file): Home Assistant asks for it when adding the device, and clients without it (or with a different one) are refused. Without `--enable-encryption` the connection is plaintext, as before. LVA warns if the key file is accessible by other users (group/other permission bits set), If the key file does not exist, LVA creates it with a new random key (readable only by its user) and logs a warning with the path of the file. The key itself is never logged: read it from the file to enter it in Home Assistant. LVA refuses to start if the key file cannot be read or does not hold 32 bytes of base64.
- When Home Assistant **reconnects** (for example after a restart), settings and entities are kept. Playback is not interrupted as long as at least one client is still connected.
- When the **last** client disconnects, LVA **stops music and assistant playback** and goes idle. It keeps waiting for Home Assistant to reconnect.
- Peripherals are told when Home Assistant is connected (`zeroconf` event with status `connected`) and when it is lost (`disconnected`).

---

## Quick reference: what happens to music?

| Situation | Music |
|-----------|-------|
| Wake word detected / button starts listening | Ducked to 50 % |
| Listening, thinking, speaking | Stays ducked |
| Conversation finished | Restored to 100 % of the set volume |
| Follow-up question (continued conversation) | Stays ducked until the conversation ends |
| Voice announcement (`assist_satellite.announce`) | Ducked, restored after |
| Media player announcement / `tts.speak` (`announce: true`), music playing | Paused during the announcement, resumed after |
| Media player announcement, music paused | Stays paused |
| Timer finishes | Ducked while ringing, restored when ringing stops |
| Stop word said | Music is not stopped, only restored from ducking |
| Mute switch toggled | Unchanged |
| Media player stop (Home Assistant / peripheral) | Stopped |
| Last Home Assistant client disconnects | Stopped |

> **Note:** This page was written from the program logic. Behavior that depends on audio hardware (exact timing of chimes, ducking audibility) depends on your sound card and audio server.
