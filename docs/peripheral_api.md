# Peripheral WebSocket API

Linux Voice Assistant exposes a WebSocket API on port 6055 that lets external scripts and containers integrate with LVA in real time: LED rings, button boards, displays and other peripherals.

The API is versioned, so a peripheral written for an older version keeps working when a newer one appears. A peripheral chooses the version with the path of the URL:

| URL | Version | Specification |
|-----|---------|---------------|
| `ws://<lva-host>:6055/v1` | 1 | [Version 1](peripheral_api/v1.md) |
| `ws://<lva-host>:6055/` (no path) | 1 | Peripherals written before versioning connect like this and keep working. |

- Clients of different versions can be connected at the same time. Each one only sees the events and commands of its own version.
- A path that is not a supported version is refused with HTTP 404, and LVA logs the versions it supports.
- Each version is its own module (`linux_voice_assistant/peripheral_api/v1.py`) with its own specification page. A change to one version never changes another. Version 1 is only changed to fix bugs.
- The behavior behind the API is described in [behavior.md](behavior.md#peripheral-api-versions).

The port and the address are set with `--peripheral-host` and `--peripheral-port`. `--disable-peripheral-api` turns the API off.
