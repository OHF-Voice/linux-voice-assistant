# Extended Docker examples: LVA and Local Audio

These are extended examples for Docker. The standard installation is described in [install_application.md](../install_application.md). The two Docker Compose projects here run LVA and the [Music Assistant Local Audio add-on](https://github.com/music-assistant/local-audio-addon) (a Sendspin player) side by side on one host, without PiCompose. LVA then ducks the player while you talk, around announcements, and shows it in the media player, see [behavior.md](../behavior.md#sendspin-player).

Each project has a `docker-compose.yml` and a `.env.example` that describes every option.

| Directory | Contents |
|-----------|----------|
| [lva/](lva/) | `docker-compose.yml` and `.env.example` of linux-voice-assistant |
| [local-audio/](local-audio/) | `docker-compose.yml` and `.env.example` of the Local Audio player |

## Usage

Copy a directory to where you want to keep it, copy its `.env.example` to `.env`, edit that and start it. Compose reads the `.env` next to the `docker-compose.yml`. The two projects are independent, the order does not matter:

```sh
cd local-audio && cp .env.example .env && docker compose up -d
cd ../lva && cp .env.example .env && docker compose up -d
```

Look at the logs with `docker compose logs -f`. Every option is described in the `.env.example` of its project.

## How the two projects find each other

LVA talks to the player through the **control socket** of `sendspin-cli`, a Unix socket file, so both containers have to run on the same host and share the directory that holds the socket (see [install_application.md](../install_application.md#sendspin-player-in-docker-local-audio-add-on)).

| What | Setting |
|------|---------|
| Directory of the socket on the host | `LOCAL_AUDIO_CONTROL_DIR` in the `.env` of `local-audio` (mounted to `/run/sendspin-cli`, where the image of the player keeps its socket) and `SENDSPIN_CONTROL_DIR` in the `.env` of `lva` (mounted at the same path in the container). Both default to `/run/sendspin-cli`. |
| Socket file | `control.sock`, LVA looks for it in `SENDSPIN_CONTROL_DIR` (`--sendspin-control-dir`) |
| User | The socket has mode `0600`, so `LVA_USER_ID` and `LOCAL_AUDIO_USER_ID` have to be the same user (1000 by default) |
| Audio server | Both need the PulseAudio/PipeWire socket of that user, see `LVA_PULSE_SERVER` and `LOCAL_AUDIO_PULSE_SERVER` |
| Switch in LVA | `ENABLE_SENDSPIN_BRIDGE` in the `.env` of `lva`: `1` (the default of the application) controls the player through the socket, `0` turns it off |

If you change the directory, set the same value as `LOCAL_AUDIO_CONTROL_DIR` and `SENDSPIN_CONTROL_DIR`, otherwise LVA does not find the socket. Without a running player LVA works as without Sendspin.

## Notes

- The player runs as a regular user with the `s6-overlay` of the image. It needs the network of the host (mDNS and the port that the server connects back to, 8928 by default, `SENDSPIN_PORT`) and an Avahi daemon on the host, which it reaches over the D-Bus socket of the host.
- On hosts with AppArmor enabled (for example Ubuntu) the default Docker profile blocks that. The compose file of `local-audio` sets `security_opt: apparmor=unconfined` for it, see [AppArmor](../install_application.md#apparmor).
- Unless `SENDSPIN_NAME` is set in the `.env` of `local-audio`, the player is named after the host in Music Assistant.
- Newer images of the player require that Music Assistant pairs with it. The `.env.example` of `local-audio` explains the options (`SENDSPIN_ALLOW_UNPAIRED` is set to `true` there for now).
- The player cannot run twice on one host with these defaults: a second player needs its own `SENDSPIN_PORT` and its own `LOCAL_AUDIO_CONTROL_DIR` (which has to match the `SENDSPIN_CONTROL_DIR` of the LVA that controls it).
