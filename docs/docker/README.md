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
| Directory of the socket on the host | Player: `/run/<container name>` (`LOCAL_AUDIO_CONTAINER_NAME`, default `local-audio`), mounted to `/control`. LVA: `LVA_SENDSPIN_CONTROL_DIR` (default `/run/local-audio`), mounted to `/sendspin`. |
| Socket file | `control.sock`, LVA looks for `/sendspin/control.sock` (`SENDSPIN_CONTROL_SOCKET`) |
| User | The socket has mode `0600`, so `LVA_USER_ID` and `LOCAL_AUDIO_USER_ID` have to be the same user (1000 by default) |
| Audio server | Both need the PulseAudio/PipeWire socket of that user, see `LVA_PULSE_SERVER` and `LOCAL_AUDIO_PULSE_SERVER` |

If you change `LOCAL_AUDIO_CONTAINER_NAME`, set `LVA_SENDSPIN_CONTROL_DIR` to the same `/run/<container name>`, otherwise LVA does not find the socket. Without a running player LVA works as without Sendspin.

## Notes

- The player needs the network of the host (mDNS and the port 8928 that the server connects back to) and an Avahi daemon on the host, which it reaches over the D-Bus socket of the host.
- On hosts with AppArmor enabled (for example Ubuntu) the default Docker profile blocks that, see [AppArmor](../install_application.md#apparmor).
- Newer images of the player require that Music Assistant pairs with it. The `.env.example` of `local-audio` explains the options.
- The player cannot run twice on one host with these defaults (container name, port and socket directory). Give every instance its own.
