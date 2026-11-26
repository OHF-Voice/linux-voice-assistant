# Peripheral API behavior

What LVA does for a peripheral (LEDs, buttons, HAT boards, the [Linux Peripherals Controller](https://github.com/OHF-Voice/linux-peripherals-controller)) that is connected to the [peripheral API](peripheral_api/readme.md), kept short, one section per API version. The protocol itself (event and command names, data) is in the specification of each version. For the rest of LVA see [behavior.md](behavior.md).

## Choosing a version

- A peripheral picks the version with the path of the URL: `ws://<host>:6055/v1`, `ws://<host>:6055/v2`. No path means version 1.
- Clients of different versions can be connected at the same time. Each one only sees the events and commands of its own version.
- An unknown version is refused (HTTP 404) and LVA logs the versions it supports.
- Versions are separate modules. Version 1 is frozen, only bugs are fixed. A version is only removed as a breaking change.

---

## Version 1

Specification: [peripheral_api/v1.md](peripheral_api/v1.md)

- LVA sends events about the voice pipeline (wake word, listening, thinking, speaking, idle, error), mute, timers, music and volume. A ringing timer that nobody stops ends on its own after `--timer-max-ring-seconds`: `timer_auto_stopped`, then `idle`. A new client first gets a snapshot, then the last state event.
- A peripheral can register Home Assistant entities (light, button events, button lock). LVA then creates them on its own device in Home Assistant.
- What a peripheral can trigger, and what happens:

| Action | Result |
|--------|--------|
| Start listening | Plays the start-listening sound and begins an interaction without a wake word. Also silences a ringing timer. Ignored while muted or while no wake word is active. |
| Stop pipeline | Stops the current reply or a ringing timer, same as the stop word. |
| Mute / unmute microphone | Same as the Mute switch. |
| Volume up / down / set | Changes the shared volume by `--peripheral-volume-step`. |
| Stop / pause / resume media | Controls the music player directly. |
| Single press | Reported to Home Assistant as a `single_press` event (no sound). |
| Double / triple / long press | Plays the matching sound (`--button-double-press-sound`, `--button-triple-press-sound`, `--button-long-press-sound`) and reports the event to Home Assistant. |

- What a physical button does is defined by the peripheral, not by LVA. The *Disable button controls* switch (if the peripheral registered it) locks the buttons from Home Assistant, and the peripheral has to honor it.

---

## Version 2

Specification: [peripheral_api/v2.md](peripheral_api/v2.md)

The reduced API for the Linux Peripherals Controller. LVA reports what it knows and does a few things on command. Everything about hardware and Home Assistant entities is the peripheral's business.

- **No Home Assistant entities.** LVA creates none for a version 2 client and reports nothing about buttons to Home Assistant.
- **Hello and status.** A client gets `hello`, then the whole status once. After that a status value is only sent when it changes. The timer is reported before the state, so a timer that a wake word or a button ended is reported as ended first, then comes `wake_word` or `listening`.
- **State.** One of `idle`, `wake_word` (wake word detected, wake sound plays), `listening`, `speech_detected` (Home Assistant hears speech, from its start until the request is being processed; without speech the state stays `listening`), `thinking`, `tts` (spoken reply), `announce` (voice or media player announcement). Muted and a ringing timer do not change it. A follow-up question goes `tts`, then `listening` again. After a button start the start-listening sound plays first, so the state stays `idle` until listening begins.
- **Errors.** A failed pipeline sends `pipeline_error` with a reason, then `idle`.
- **Timer not dismissed.** A ringing timer that nobody stops ends on its own after `--timer-max-ring-seconds`. `timer_auto_stopped` (with how long it rang) is sent first, then `timer_status` becomes `idle`. A timer that someone stops (button, stop word, wake word) does not send it.
- **Mute, volume, connection.** `mute_status` is the microphone mute. `volume_status` is 0 to 100 and shows 0 while the media player is muted. `lva_status` is `connected` while Home Assistant is connected.
- **Timers.** `timer_status` is `active` while one counts down, `ringing` while one rings, else `idle`. The time left and its percentage of the whole belong to the timer that ends first, are counted down once a second and are 0 when no timer counts down.
- **Music.** `media_player_status` is `idle`, `playing`, `silent` (playing at 0 %) or `pause`. Music paused only for ducking counts as `playing`. During an announcement the value stays as it was.
- **Hardware mute.** Some hardware can mute its microphone itself, and sometimes software can switch that. The peripheral reports it with the commands `hardware_mute_type` (`none`, `changeable` if software can switch it, `read_only` if it can only be read) and `hardware_mute_status` (`muted` or `unmuted`). LVA keeps them **for its own processing only**: it does not change its own mute (the Mute switch, `mute_status`), does not pass them on to other clients and creates no Home Assistant entity, because the peripheral shows the hardware mute in Home Assistant itself. The device is always the authority: the peripheral reads the state from the device and reports it after every (re)connect, and LVA never pushes its own mute onto the hardware when a peripheral connects. If the type is `changeable`, LVA can ask the hardware to mute or unmute with the events `hardware_mute_on` and `hardware_mute_off`, which are ignored for any other type. LVA does not send them yet: the sync of its own mute with the hardware mute is planned (see [todo.md](../todo.md)).
- **Commands.**
  - `start_listening`: like the start-listening button. Ignored while muted, with no wake word active, or while a pipeline runs. Also silences a ringing timer.
  - `stop`: stops the reply, announcement and pipeline, like the stop word. Ignored while a timer rings, and never touches the music.
  - `timer_stop`: stops a ringing timer. Ignored if none rings.
  - `mute_on`, `mute_off`: like the Mute switch, with its sound. Ignored if already in that state. A peripheral can also unmute while `--enable-remote-unmute 0` (only Home Assistant is kept from it, see [behavior.md](behavior.md#mute)), and Home Assistant is told through the Mute entity, which is a read-only sensor then.
  - `volume_up`, `volume_down`: change the volume by `--peripheral-volume-step` and play the volume step sound (`sounds/volume_step.wav`). At the limit (`volume_up` at 100 %, `volume_down` at 0 %) nothing changes and the volume limit sound (`sounds/volume_limit.wav`) plays instead. Neither sound has an option.
  - `volume_set`: sets 0 to 100 %, values outside are clamped, no sound.
  - `button_pressed`: LVA plays the sound for `double`, `triple` and `long` (`--button-double-press-sound`, `--button-triple-press-sound`, `--button-long-press-sound`). `single` plays nothing.
- **Effect as in Home Assistant.** Commands change the assistant like the same action from Home Assistant, which sees the new volume, mute and media state.
- **Invalid commands** are ignored and logged as a warning. The connection stays open.
