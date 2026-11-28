# Behavior Guide

This page describes what Linux Voice Assistant (LVA) does in everyday situations, from the user's point of view: what happens to music when you talk to the assistant, what happens when a timer rings, what the stop word does, and so on.

For the list of Home Assistant entities see [configuration.md](configuration.md). For the events sent to hardware peripherals see [peripheral_api/readme.md](peripheral_api/readme.md), for what peripherals can trigger see the specification of each version ([v1](peripheral_api/v1.md), [v2](peripheral_api/v2.md)).

---

## Contents

- [Basics: two audio channels](#basics-two-audio-channels)
- [Talking to the assistant](#talking-to-the-assistant)
- [Music and media playback](#music-and-media-playback)
  - [Sendspin player](#sendspin-player)
- [Announcements](#announcements)
- [Timers](#timers)
- [The stop word](#the-stop-word)
- [Mute](#mute)
- [Volume](#volume)
- [Peripheral API versions](#peripheral-api-versions)
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

- **Music is never ended by the assistant on its own.** During a conversation it is lowered (*ducked*). During a TTS / announcement sent to the media player (for example `tts.speak` with `announce: true`) it is **ducked** for the duration of the announcement and then restored, and the media player reports `announcing`. Only an explicit *stop* from Home Assistant or a peripheral, or losing the connection to Home Assistant (see below), stops it for good.
- **Ducking** means the music volume is reduced to **50 %** of the volume you set. When the interaction is over it returns to 100 % of your set volume. The music stays ducked as long as something still wants it ducked: a conversation, a ringing timer, a voice announcement and a media player announcement ask for it separately, so the music returns to full volume only when all of them are over. For example, a timer that is stopped during a conversation does not end the ducking of the conversation. When the last Home Assistant client disconnects, all of them are released, except the one of a ringing timer, which keeps ringing.
- **Pause mode (`--mpv-player-duck-mode pause`, `MPV_PLAYER_DUCK_MODE=pause`).** The default is `duck`, which lowers the volume as described above. With `pause` the music of LVA is **paused** instead of lowered, and resumed when the last reason for ducking is over. Music that was already paused stays paused. A pause or stop from Home Assistant or a peripheral during that time takes over, so the music is not resumed against it. While it is paused for ducking, the media player keeps reporting `playing`. Music that is started while it is ducked plays at full volume, because nothing pauses it again. The option only applies to the music player, not to the voice player (announcements, chimes, TTS, timer sound).

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
- **Starting by button.** A peripheral button can start listening without a wake word. Instead of the wake sound, the start-listening sound (`--start-listening-sound`) plays; everything else is identical. This also silences a ringing timer. It does nothing while no wake word is active, see below.
- **Errors.** If the pipeline reports an error, version 1 peripherals receive a `pipeline_error` event, version 2 peripherals the state `pipeline_error`. See [peripheral_api/readme.md](peripheral_api/readme.md).
- **Two wake words.** Home Assistant can activate up to two wake words at once. Each has its own sensitivity setting (*Wake Word 1/2 Sensitivity*). A word keeps its slot while the other one changes, and always uses the sensitivity of its own slot, also when it is the only active word.
- **Wake word locations.** Wake words are read from `wakewords/internal`, `wakewords/downloaded` and `wakewords/custom` (root: `--wake-word-root-dir`), each with a `microwakeword` and an `openwakeword` subdirectory. The same model in two directories is two separate wake words. `--wake-model` and `--wake-model-2` accept the file name (`hey_jarvis`) or the id of the wake word (`Internal - MWW - Hey Jarvis`). If a file name exists in more than one origin, `custom` wins over `downloaded`, which wins over `internal`. The same applies to the stop word. Wake words that Home Assistant sends are stored in `downloaded`.
- **Wake words from Home Assistant.** Home Assistant offers the microWakeWord models from its `custom_wake_words` folder for download; they appear in the wake word selection as `Download from HA - <phrase>` (for example `Download from HA - Hey Computer`), unless the file is already in `downloaded` (same file name). If several offers have the same phrase, only the first one is listed (logged at debug level). When one is chosen, LVA downloads it into `downloaded` in the background and activates the new selection when the download has finished. Until then the previous wake words stay active, and the assistant keeps working (Home Assistant connection, peripherals, timers). A model already on disk with the same size and SHA-256 is not downloaded again. If a newer selection arrives during a download, the older selection is not applied. A failed download (network error, Home Assistant not answering for 30 s, HTTP error) is logged as a warning and the rest of the selection becomes active; it leaves no partial file in `downloaded`. Every offered wake word and the reason why it is offered or not is logged at debug level. After a download its name changes to the plain phrase, so LVA makes Home Assistant reload the wake word list (see *Wake word names*).
- **Wake word names.** Each wake word has an id made of origin, engine and the phrase from its `.json` file, for example `Internal - MWW - Hey Jarvis`. The id is stored, sent to peripherals and logged, and Home Assistant sends it back when a wake word is chosen. Home Assistant shows a different name: microWakeWord models are named by their plain phrase (`Hey Jarvis`), openWakeWord models by their phrase with ` (OWW)` (`Hey Jarvis (OWW)`). LVA adds the suffix when it loads the model; the `.json` file is not changed. On a detection LVA sends this name as the phrase, because Home Assistant compares it with its wake word select to choose the pipeline, and the Voice support app recognizes the plain microWakeWord phrases (`Okay Nabu`, `Hey Jarvis`, `Hey Mycroft`). Each offered wake word and its name are logged at debug level.
- **One wake word per name.** Home Assistant shows only one option per name, so within one engine LVA offers only one wake word per phrase: `custom` wins over `downloaded`, which wins over `internal`. Phrases are compared without case and surrounding spaces. Each hidden wake word is logged at debug level. A microWakeWord whose phrase is the name of an openWakeWord (for example a custom microWakeWord with the phrase `Hey Jarvis (OWW)`) wins over it, and an error is logged. If two wake words of one origin and engine have the same phrase, only the last one found is used and a warning is logged.
- **Hidden active wake word.** If an active wake word is hidden by another one with the same name (for example a custom `Hey Jarvis` was added while the internal one was active), LVA switches to the offered one when it loads the wake words at startup and logs it. If that one is already in the other slot, the slot becomes empty.
- **Reloading the wake word list in Home Assistant.** Home Assistant reads the list of wake words only when it connects. When the names change, Home Assistant no longer finds the option its wake word select had and shows *No wake word*. After a download, LVA closes every connection 1 s after the download, so Home Assistant reconnects at once and reads the new list; after the update that introduced plain phrases, the first connection already reads it. In the handshake with the new list, LVA closes the connection once more 1 s later, and in the handshake after that it reports only the first active wake word, because Home Assistant restores its first wake word select only from *No wake word* and only with exactly one active wake word. Music of LVA's music player keeps playing during these reconnects, also if Home Assistant does not come back. Everything else behaves as on any disconnect (TTS stops, ducking ends, a ringing timer keeps ringing). With two active wake words, Home Assistant's second select shows *No wake word* afterwards, while LVA keeps listening for both; a warning says to choose it again. Until then, changing the first select in Home Assistant deactivates the second wake word, and the second wake word starts the pipeline of the first assistant, because Home Assistant chooses the pipeline by the text of its select.
- **Stored wake words.** `preferences.json` stores each active wake word with its origin, engine and file name, for example `{"origin": "internal", "engine": "mww", "file_id": "hey_jarvis"}`. Once after the update that introduced this, wake words stored under a former id (`hey_jarvis`) or a name (`Hey Jarvis`, `Hey Jarvis (OWW)` or `Internal - MWW - Hey Jarvis`) are replaced by such an entry and a message is logged. A plain phrase means the microWakeWord model. Values that match nothing are kept and then skipped at startup with a warning. A former id that Home Assistant still sends is resolved to the wake word as well. The numbers of the migrations that ran are stored in `applied_migrations`.
- **No wake word active.** When both wake word selects in Home Assistant are set to *No wake word*, no wake word starts the assistant, and LVA keeps running without any load. A button cannot start listening either, as it does not while muted. The stored wake words are then empty, so after a restart the default wake words (`--wake-model`) are active again.
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

If music is paused when you start talking to the assistant, it stays paused. Ducking only changes the volume (or pauses the music in `pause` mode, which resumes only music that it paused itself). The music is not resumed automatically at the end of a conversation.

### Sendspin player

A Sendspin player (`sendspin-cli`, for example the [Music Assistant Local Audio add-on](https://github.com/music-assistant/local-audio-addon)) plays outside LVA, so LVA cannot control it through mpv. LVA drives it through the control socket of the player (`control.sock` in `--sendspin-control-dir`, default `/run/sendspin-cli`) and treats it as the music of the media player entity. This is the Sendspin bridge, on by default (`--enable-sendspin-bridge 1`, `ENABLE_SENDSPIN_BRIDGE=1`). With `--enable-sendspin-bridge 0` LVA leaves the player alone and works as without it.

| Situation | Sendspin player / media player entity |
|-----------|---------------------------------------|
| Sendspin plays and LVA's own music player does not | The entity reports `playing`. In every other case (paused, stopped, unknown, not connected to a server, socket unreachable) it reports `idle`, like an ESPHome device: LVA counts only `playing` as playing. LVA's own music has priority: while it plays or is paused, the entity shows that. |
| Pause / Stop from Home Assistant while Sendspin is `playing` | Sent to the Sendspin player (`pause`, `stop`) instead of LVA's music player. The entity then reports `idle`. |
| Play from Home Assistant while LVA's own music neither plays nor is paused | Sent to the Sendspin player (`play`), which continues where it stopped. If LVA's music is paused, Play resumes it. |
| Music started from Home Assistant (`media_player.play_media` without `announce`) while Sendspin is `playing` | Sendspin is **stopped** and LVA's music plays, so only one of them plays. `stop` is a command for the whole Sendspin group. |
| Sendspin starts playing while LVA's own music plays or is paused | LVA's music is **stopped**, so only one of them plays. |
| Sendspin starts playing | Peripherals get `media_player_playing` (version 1), like for music from Home Assistant. Not while a conversation is running, so it does not replace the state of the conversation. |
| Volume or mute set on the media player entity (Home Assistant or peripheral) | Applied to LVA's own volume as before **and** sent to the Sendspin player as its group volume / mute. |
| Volume or mute changed at the Sendspin group (Music Assistant, another player) while Sendspin plays | The entity follows it, so the volume of the card and LVA's shared volume ([Volume](#volume)) change too. Not taken over while LVA ducks the player (in every duck mode, until the last reason for ducking is over, because the group volume is then not the real volume) and for 2 s after LVA set the volume itself. A change made during that time is taken over with the next status after the ducking. |
| `media_player.play_media` with `sendspin://current` | Resumes the Sendspin player. |
| `media_player.play_media` with any other `sendspin://` URL | Ignored with a warning. LVA cannot make the player connect to a server, the player announces itself over mDNS. |
| Ducking (wake word, button, conversation, voice or media player announcement, timer ringing) | Depends on `--sendspin-player-duck-mode`, see below. The ducking ends when the last of them is over, like for LVA's own music. |
| Last Home Assistant client disconnects | The Sendspin player is **not** stopped and keeps playing. Ducking that ends with the connection ends for it too. |

**Ducking modes** (`--sendspin-player-duck-mode`, `SENDSPIN_PLAYER_DUCK_MODE`):

- **`pulse` (default).** LVA lowers the **sound stream** of the player in PulseAudio/PipeWire to 50 % of its volume and gives it its volume back afterwards. The player keeps playing. LVA itself sends nothing to the Sendspin group, but the Local Audio add-on notices the change of its stream volume and passes it on to its Sendspin group as a change in percent. So the other players of the group are ducked as well, as expected, and Music Assistant shows the lowered group volume. When LVA gives the stream its volume back, the group follows again. (Observed with the Local Audio add-on, in the LVA log as group volume 10 → 5 → 10 during a conversation; not checked in the sources of `sendspin-cli`.) LVA finds the stream by its application name `sendspin-cli` (`application.name`, or `application.process.binary` when the player plays through the ALSA plugin). It needs the same sound server as the player (in Docker `PULSE_SERVER` of both containers), and the control socket to see that the player plays: like the other modes, `pulse` acts only while the player plays, a player that is paused, stopped or not connected to a server is left alone until it starts playing while ducking is still active.
  - A player that starts playing while ducking is active is ducked within about a second, when LVA reads its status. Once LVA has asked for its stream to be ducked, a new stream of the player (for example after the player restarted it) is lowered as soon as it appears. Only a stream at the volume it had before ducking is lowered, and only a stream still at the ducked volume gets its volume back: a stream whose volume someone changed in the meantime is left alone.
  - The sound server remembers the last volume of an application (stream-restore) and gives it to the next stream of the player. So LVA keeps the volume before and after ducking until it is restored: in `preferences.json` (`sendspin_stream_ducked_from`, `sendspin_stream_ducked_to`), also across a restart of LVA. If the player has no stream when ducking ends, the next stream that starts at the ducked volume gets its volume back.
  - If the sound server cannot be reached, LVA logs a warning once and tries again after 1 s, then 2 s, 4 s and so on up to every 60 s. Nothing is ducked meanwhile. If the player plays but no stream of it is found when ducking starts (for example because it plays through ALSA), nothing is ducked and LVA logs an error (`Cannot duck: no stream of sendspin-cli found in the sound server`). LVA does not switch to another mode.
  - All channels of the stream get the same volume (the mean of the channels before), so a balance set on the stream is lost by ducking.
  - LVA does not take this lowered group volume over into its own volume (see the table above), so the reply of the assistant is not lowered with it.
- **`stop`.** If the player is **playing**, it is **stopped** and started again when ducking ends. The volume is not touched. The stop applies to the whole Sendspin group. A player that is paused, stopped or not connected to a server is left alone, unless it starts playing while ducking is still active: then it is stopped within about a second.
- **`duck`.** If the player is **playing**, the volume of the **whole Sendspin group** is set to 50 % of its current value and restored when ducking ends. Every player of the group and Music Assistant follow it. A volume change made by someone else while it is ducked is overwritten when ducking ends. A volume set on the media player entity meanwhile becomes the real volume: the group stays ducked relative to it and is restored to it. LVA keeps the volumes before and after ducking in `preferences.json` (`sendspin_ducked_from`, `sendspin_ducked_to`). If LVA starts and finds them, it sets the group volume back once the player answers, but only if it still is the ducked value.

Further details:

- The status is read from the player once a second (`status-kv`), so a change shows up in Home Assistant after up to about a second.
- Without a Sendspin player the socket does not exist and nothing is controlled through it. If the socket is missing, cannot be opened or the player does not answer within 1 s, nothing is paused, stopped or mirrored and the entity reports `idle` for Sendspin. LVA logs a critical message once when the socket becomes unreachable (including at startup) and an info message once when it is reachable again. While it is unreachable, the socket is tried less often: every 5 s after 30 s, every 30 s after 120 s and every 60 s after 600 s. `pulse` ducking needs the socket too, to see that the player plays.
- A stop that Music Assistant sends keeps the position, and `sendspin-cli` then reports `paused`. LVA treats everything that is not `playing` as stopped.
- A stop does not take the player out of its group. The control socket of `sendspin-cli` has no command for that.
- Whether Sendspin is supported and the pairing token of the player are not announced to Home Assistant.

#### Control socket protocol

Plain text over a Unix stream socket, one request per connection:

1. LVA connects and sends one line, for example `status-kv\n`, `vol 25\n`, `pause\n` or `play\n`.
2. The player answers with `ok` or `error <kind>: <reason>` as the first line and closes the connection. `status-kv` continues with `key=value` lines.
3. LVA reads `state` (`playing`, also `playing (speed …)`; anything else counts as idle), `group_volume` and `group_muted` from `status-kv`.
4. Other requests LVA sends: `play`, `pause`, `stop`, `vol <0-100>` and `mute on|off`.

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

Triggered by a `media_player.play_media` call with `announce: true` on the LVA media player, which is also what `tts.speak` with `announce: true` does. The music is **ducked to 50 %** while the announcement is spoken, like on ESPHome devices, and the media player reports `announcing` until the announcement is over. A Sendspin player is ducked as well, see [Sendspin player](#sendspin-player).

| Music state when the announcement arrives | What happens |
|-------------------------------------------|--------------|
| **Playing** | The music is ducked to 50 %, the announcement plays, then the music returns to full volume. The media player reports `announcing` meanwhile and `playing` afterwards. |
| **Paused** (by you) | The announcement plays, the music stays paused. The media player reports `announcing` meanwhile and `paused` afterwards. |
| **Not playing** | The announcement plays. The media player reports `announcing` meanwhile and `idle` afterwards. |

A second announcement replaces the first one. A media player announcement asks for the ducking on its own, like a conversation, a ringing timer and a voice announcement do, so the music returns to full volume only when all of them are over. For example, a media player announcement that ends during a voice announcement does not end its ducking.

---

## Timers

Timers are created by voice ("set a timer for 5 minutes") and managed by Home Assistant. LVA keeps its own list of all timers, with the statuses of Home Assistant (`active`, `paused`, `finished`, `cancelled`), counts them down itself and rings them. The list is stored in `preferences.json` on every change, so it outlives a lost connection and a restart of LVA.

| Event | What happens |
|-------|--------------|
| Timer started / updated | The timer is added to the list or its end time is set again. A timer that Home Assistant reports as paused is kept with the time it had left and does not count down. Peripherals receive `timer_ticking` / `timer_updated` with the remaining time (for example to show a countdown on LEDs). |
| Timer cancelled | The timer is set to `cancelled`. If it was ringing, its ringing stops. Peripherals receive `timer_updated` with 0 seconds left. If no other timer counts down, is paused or rings, the assistant returns to idle. If another one counts down or is paused, peripherals receive `timer_ticking` for it instead (the one that ends first). |
| **Timer finished** (from Home Assistant) | The timer is due now, so it rings at once, see the next line. If it is `finished` already (it rings, or its ringing has ended), nothing happens. |
| **Time is up** | LVA checks its list once a second. A timer whose end time has passed starts ringing, whether Home Assistant is connected or not: the music is **ducked to 50 %**, the stop word becomes active, peripherals receive `timer_ringing` and the timer sound (`--timer-finished-sound`) plays on repeat with a 1 second pause between repetitions. |
| A second timer finishes while one rings | Peripherals receive `timer_ringing` for it too. The sound keeps playing once, it does not play twice. |
| Timer is ringing and you say the **stop word** | **All** ringing timers stop, music is restored, the assistant goes idle. |
| Timer is ringing and you say the **wake word** or press the **start-listening button** | All ringing timers stop and a normal interaction begins. |
| Timer is ringing and a peripheral sends `stop_timer_ringing` or `stop_pipeline` | All ringing timers stop, music is restored, the assistant goes idle. |
| Nobody reacts | Each timer **stops ringing automatically** `--timer-max-ring-seconds` (default 900 s = 15 minutes) after it started ringing. Peripherals receive `timer_auto_stopped` (with how long it rang) for it. When the last ringing timer has stopped, the music is restored and peripherals receive `idle`, so they can show that the timer was not dismissed. |

A timer that stopped ringing stays `finished` and does not ring again. `finished` and `cancelled` timers are deleted from the list 7 days after they ended, a `paused` timer that Home Assistant did not report in its last list (or if there is no list) 7 days after its last change (info log).

**Without Home Assistant.** When the last Home Assistant client disconnects, the timers keep counting. A ringing timer keeps ringing, the stop word stays active for it and the music stays ducked for it. Conversation, announcement and music playback are stopped as before. While Home Assistant is offline, timers finish and ring as usual, and the stop word, the buttons and the peripheral commands stop them (`start_listening` only stops the ringing, there is no pipeline to start). The wake word is not listened for. Timers cannot be created or cancelled by voice.

**After a restart of LVA.** The stored list is loaded. A timer that was ringing rings again right after the start, for the full `--timer-max-ring-seconds`. A timer whose time ran out while LVA was not running rings right after the start.

**Merge with the list of Home Assistant.** Home Assistant sends no timer list when LVA connects, and the timer events of a disconnect are lost. So after every connect (also at the start of LVA), once Home Assistant accepts actions from the device, LVA asks for its own timer list with the action `timer_list.get_timers` and merges it by timer id:

| Case | What LVA does |
|------|---------------|
| In Home Assistant `active` or `paused`, not in LVA | Taken over. |
| In Home Assistant and in LVA, different | The values of Home Assistant are taken (end time, time left, status). A timer that rang in LVA and is running again in Home Assistant stops ringing. |
| In Home Assistant `finished`, in LVA `active` or `paused` | It rings right away. |
| In Home Assistant `cancelled`, in LVA not | Set to `cancelled`. If it was ringing, its ringing stops. |
| In Home Assistant `finished` or `cancelled`, not in LVA | Ignored. |
| The same in both | Nothing. |
| Not in Home Assistant, in LVA | Kept, because Home Assistant forgets its timers when it restarts. It rings when its time is up. |

If the list changed and no timer rings, peripherals receive `timer_ticking` for the timer that is shown. If Home Assistant cannot perform the action (no `timer_list` yet, or the ESPHome option *Allow the device to perform Home Assistant actions* is off) or does not answer within 10 s, LVA logs a hint once per run (info) and works with its own list and the events only.

**Known limits.**

- Without the merge (see above), LVA only hears about timers through the events Home Assistant sends while it is connected: a timer cancelled in Home Assistant while LVA was not connected still rings in LVA. With the merge, a timer that is created and ends completely while LVA is not connected still does not ring, because Home Assistant reports it as `finished` and finished timers unknown to LVA are ignored.
- A timer cancelled in Home Assistant and evicted from its archive before LVA reconnects looks like one that Home Assistant forgot in a restart. LVA keeps it and rings it.
- After a restart of Home Assistant, the timers still in LVA cannot be cancelled by voice (Home Assistant has forgotten them). They end when they ring and are stopped, or after `--timer-max-ring-seconds` of ringing.
- Stopping a ringing timer is not reported to Home Assistant.
- An announcement while a timer rings replaces the timer sound. The timer still counts as ringing, but its sound does not come back and, once the announcement is over, the stop word no longer stops it. The wake word, the buttons, the peripheral commands and `--timer-max-ring-seconds` still end it. This was already so before LVA kept its own timer list.

Notes:

- A timer finishing does not stop or pause the music. It is only ducked, so a ringing timer is audible over it.
- While the assistant is muted, the stop word and the wake word do not work. Unmute first, or stop the ringing with a peripheral button.
- **Time.** The end time of a timer comes from Home Assistant, so LVA compares its timers on the time of Home Assistant. It asks Home Assistant for its time right after connecting and then every 30 s while connected, and keeps the difference to its own clock (accurate to about half a second). The difference is not stored. Until the first answer after a start of LVA, LVA uses its own clock; offline after a connection, it uses the last known difference. If the difference is more than 10 s, an error is logged at every check: the clock of the device is probably not set. If LVA starts without Home Assistant and its clock is not set yet, stored timers ring too early or too late until the clock is set or Home Assistant is connected.

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
- The mute state is saved in `preferences.json` and restored after a restart. A device that was muted stays muted: the wake word and the stop word stay disabled and no audio is sent until it is unmuted. No mute sound plays at startup. Home Assistant's **Mute** switch (or **Mute** sensor) and the first peripheral snapshot (`muted` in `snapshot`) show the restored state right away. With `--enable-remote-unmute 0` the restored mute can only be undone by a peripheral.

When unmuted, the unmute sound (`--unmute-sound`) plays and the assistant returns to idle.

**Remote unmute.** `--enable-remote-unmute` (`ENABLE_REMOTE_UNMUTE`, `1` by default) decides who may unmute:

- `1`: Home Assistant's **Mute** switch and peripherals (`mute_mic` / `unmute_mic` in peripheral API version 1, `muted_on` / `muted_off` in version 2) can mute and unmute.
- `0`: only a peripheral can unmute (`unmute_mic` / `muted_off`, for example a physical mute switch). Home Assistant has no **Mute** switch, it shows the mute state in a read-only **Mute** sensor instead.
- The setting is only read at startup and is not saved in `preferences.json`.

Do not confuse this with the **media player mute**, which sets the shared volume to 0, see below.

Some hardware can also mute its microphone itself. That **hardware mute** is separate from this switch: LVA only learns about it from a version 2 peripheral, see [peripheral_api/v2.md](peripheral_api/v2.md#hardware-mute). LVA does not mute because the hardware is muted and the other way round, and Home Assistant sees the hardware mute through the peripheral, not through LVA.

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

## Peripheral API versions

The peripheral API is versioned. Choosing a version and what LVA does in each version is described in [peripheral_api/readme.md](peripheral_api/readme.md) and the specification of each version.

---

## Connection to Home Assistant

- LVA is discovered by Home Assistant via mDNS (ESPHome integration). Several clients can be connected at the same time.
- **Without mDNS.** With `--enable-zeroconf 0`, LVA announces nothing on the network, so Home Assistant does not discover it. Add it manually in the ESPHome integration with the host IP address and port `6053` (`--port`). Everything else (ESPHome API, peripheral API, `zeroconf` peripheral events) works as before.
- **Identity.** Home Assistant knows the device by its MAC address, which is also in its name (`lva-<mac>`): by default the network interface's. With `--mac-address` it is that one instead, so the device stays the same in Home Assistant when the machine uses another interface, and several instances on one machine get one identity each.
- **Encryption.** With `--enable-encryption`, the connection is encrypted as on ESPHome devices with an API encryption key. The key is read from `--encryption-key-file` (default: `encryption.key` next to the preferences file): Home Assistant asks for it when adding the device, and clients without it (or with a different one) are refused. Without `--enable-encryption` the connection is plaintext, as before. LVA warns if the key file is accessible by other users (group/other permission bits set), If the key file does not exist, LVA creates it with a new random key (readable only by its user) and logs a warning with the path of the file. The key itself is never logged: read it from the file to enter it in Home Assistant. LVA refuses to start if the key file cannot be read or does not hold 32 bytes of base64.
- **Dead connections.** If a connection goes silent without being closed (a Wi-Fi drop or a DHCP renewal can cause this), LVA lets the kernel close it after about 10 s: 4 s without traffic, then 3 unanswered keepalive probes 2 s apart, or 10 s of unacknowledged data. It is then handled like any other disconnect (see below). The timings are fixed. A network stall longer than 10 s therefore ends the connection, and Home Assistant reconnects. This relies on the socket options of the Linux kernel. LVA sets each option on its own. If the kernel refuses some, the connection still gets keepalive with the options that worked, but may be closed later than 10 s, or not at all if the basic keepalive switch is refused. LVA then logs a warning that names the options that could not be set.
- **Changing network address.** The ESPHome server listens on the IPv4 address of the network interface (`--network-interface`, default: the interface of the default gateway) that LVA had at startup. LVA reads the interface address again whenever **a** client disconnects, even if others stay connected, and every 30 s as a fallback for changes that cause no disconnect. If it is **different**, LVA starts listening on the new address, stops listening on the old one and announces the new address over mDNS (without restarting mDNS). If it is the same, nothing happens.
  - If the interface has **no address** yet, or moving fails, LVA tries again after 1 s, then 2 s, 4 s and so on up to every 60 s, until it succeeds. Without mDNS (`--enable-zeroconf 0`) only the listener moves.
  - With `--host` the address is fixed by the user and is never checked or replaced.
  - A connection that is still open when the address changes is not closed by the move, keepalive ends it if it is dead.
- When Home Assistant **reconnects** (for example after a restart), settings and entities are kept. Playback is not interrupted as long as at least one client is still connected.
- When the **last** client disconnects, LVA **stops music and assistant playback** and goes idle. A Sendspin player is not stopped, see [Sendspin player](#sendspin-player). It keeps waiting for Home Assistant to reconnect. A ringing timer is the exception: it keeps ringing, see [Timers](#timers).
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
| Media player announcement / `tts.speak` (`announce: true`), music playing | Ducked during the announcement, restored after |
| Media player announcement, music paused | Stays paused |
| Timer finishes | Ducked while ringing, restored when ringing stops |
| Stop word said | Music is not stopped, only restored from ducking |
| Same situations with a Sendspin player | While it plays: its stream lowered to 50 % (`pulse`, default), stopped (`stop`) or its group volume lowered to 50 % (`duck`), see [Sendspin player](#sendspin-player) |
| Mute switch toggled | Unchanged |
| Media player stop (Home Assistant / peripheral) | Stopped |
| Last Home Assistant client disconnects | Stopped |

> **Note:** This page was written from the program logic. Behavior that depends on audio hardware (exact timing of chimes, ducking audibility) depends on your sound card and audio server.
