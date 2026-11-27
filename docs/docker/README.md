# LVA together with the Local Audio player (Sendspin)

The [Music Assistant Local Audio add-on](https://github.com/music-assistant/local-audio-addon) is a Sendspin player (`sendspin-cli`): Music Assistant plays music on it. Next to LVA on the same host, LVA lowers it while you talk, around announcements and while a timer rings, and shows it in its media player in Home Assistant. What LVA does with it is described in [behavior.md](../behavior.md#sendspin-player).

[local-audio/](local-audio/) holds a Docker Compose project for the player outside Home Assistant OS: a copy of the `docker-compose.yml` and `.env.example` of the add-on, with the output set to the sound server (`SENDSPIN_OUTPUT="pulse"`). LVA itself runs with the `docker-compose.yml` in the root of this repository, see [installation_part2_application.md](../installation_part2_application.md).

## Usage

Copy the directory to where you want to keep it, copy its `.env.example` to `.env`, edit that and start it. The two Compose projects are independent, the order does not matter:

```sh
cd local-audio && cp .env.example .env && docker compose up -d
```

Look at the logs with `docker compose logs -f`. Every option is described in the `.env.example`.

## How LVA and the player work together

LVA uses two ways to reach the player. Both work only on the same host.

| What | Needed for | Setting |
|------|------------|---------|
| **Sound server** (PulseAudio or PipeWire of the host user) | Lowering the stream of the player while ducking (`--sendspin-player-duck-mode pulse`, the default). The player passes the change on to its Sendspin group, so the other players of the group are ducked too. | The player has to play through the sound server: `SENDSPIN_OUTPUT="pulse"` (or `pipewire`) in the `.env` of `local-audio`. Both containers use the sound server of the same user: `LOCAL_AUDIO_PULSE_SERVER` and `LVA_PULSE_SERVER` |
| **Control socket** of `sendspin-cli`, a Unix socket file `control.sock` | The state of the player in the media player of LVA, play/pause/stop, volume and mute from Home Assistant, and ducking in every mode (LVA ducks only while the player plays) | Both containers mount the same host directory: `LOCAL_AUDIO_SOCKET_DIR` in the `.env` of `local-audio` and `SENDSPIN_CONTROL_DIR` in the `.env` of LVA. Both default to `/run/sendspin-cli/socket` |
| **User** | The control socket and the sound server | The socket has mode `0600`, so `LVA_USER_ID` and `LOCAL_AUDIO_USER_ID` have to be the same user (1000 by default) |
| **Switch in LVA** | | `ENABLE_SENDSPIN_BRIDGE` in the `.env` of LVA: `1` (default) on, `0` off |

If you change the directory of the socket, set the same value as `LOCAL_AUDIO_SOCKET_DIR` and `SENDSPIN_CONTROL_DIR`, otherwise LVA does not find it. Without a running player LVA works as without Sendspin.

## Connecting a player that already runs

If the player already runs from another Compose file or the stock image:

1. **Share the directory of the socket.** The image creates the socket at `/run/sendspin-cli/socket/control.sock` (`SENDSPIN_SOCKET_DIR`). Mount a host directory there, for example `- /run/sendspin-cli/socket:/run/sendspin-cli/socket`, and set the same directory as `SENDSPIN_CONTROL_DIR` in the `.env` of LVA (the default). Share the directory, not the socket file, so that the player can create the socket again when it restarts.
2. **Run the player as the user of LVA**, so that LVA may open the socket (mode `0600`). The Compose project in [local-audio/](local-audio/) does that. For a quick test only, `docker exec <player> chmod 666 /run/sendspin-cli/socket/control.sock` opens it until the player restarts, and every local user can then control the player.
3. **Let it play through the sound server** (`SENDSPIN_OUTPUT="pulse"`), so that LVA can lower its stream.
4. **Check.** `docker logs linux-voice-assistant 2>&1 | grep -i sendspin` shows `Sendspin control socket /run/sendspin-cli/socket/control.sock is reachable` and, once the sound server is reached, `Connected to the sound server, ducking the stream of sendspin-cli there`. A critical message `is not reachable` means that the socket is missing (is the directory mounted into both containers?) or that LVA may not open it (step 2). `docker exec <player> sendspin-cli status --control-socket /run/sendspin-cli/socket/control.sock` shows what the player reports, `pactl list sink-inputs` on the host shows its stream as `sendspin-cli`.

## Notes

- The player needs the network of the host (mDNS and the port the server connects back to, 8928 by default, `SENDSPIN_PORT`) and announces itself through the Avahi of the host over its D-Bus socket.
- On hosts with AppArmor enabled (for example Ubuntu) the default Docker profile blocks that, and the log of the player shows `could not register _sendspin._tcp: dns_sd error -65553`. The Compose file of `local-audio` therefore sets `security_opt: apparmor=unconfined` (this removes the AppArmor protection of that container).
- Unless `SENDSPIN_NAME` is set, the player is named after the host in Music Assistant.
- Music Assistant has to pair with the player. The `.env.example` of `local-audio` explains the options (`SENDSPIN_ALLOW_UNPAIRED` is `true` there for now).
- A second player on one host needs its own `SENDSPIN_PORT` and its own socket directory, which has to match the `SENDSPIN_CONTROL_DIR` of the LVA that controls it.
- The Compose project is a copy of the one in the add-on repository and is kept in step with it by hand.
