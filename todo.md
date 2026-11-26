# To do

## Sync the mute of LVA with the hardware mute

Some hardware can mute its microphone itself. A version 2 peripheral (the Linux Peripherals Controller) reports it to LVA with
`hardware_mute_type` (`none`, `changeable`, `read_only`) and `hardware_mute_status` (`muted`, `unmuted`). LVA keeps both for its own
processing and does nothing with them yet: its own mute and the hardware mute are independent.

Planned: a parameter (name and default still open) that **syncs** the two, so that the mute of LVA and the hardware mute show the same
state.

What is already decided:

- **The device is always the authority.** When a peripheral connects (or connects again), the state is read from the device and
  reported to LVA. LVA never pushes its own mute onto the hardware at that moment. With the sync on, LVA takes over the state of the
  device.
- After that the hardware mute is switched normally: from Home Assistant through the entity of the peripheral, or by the hardware
  itself. LVA can also switch it with `hardware_mute_on` / `hardware_mute_off` if the type is `changeable` (the API and
  `PeripheralAPIServer.request_hardware_mute_sync()` exist, nothing calls them yet).

What is still open:

- The direction for each type: with `changeable` in both directions (the mute of LVA switches the hardware and the other way
  round), with `read_only` only from the hardware to LVA, with `none` nothing.
- Whether the mute and unmute sounds play when the change comes from the hardware.
- How to avoid a loop (the mute of LVA asks the hardware, the hardware reports, LVA mutes again).
- What happens to a running reply or announcement when the hardware gets muted, as for the Mute switch (`docs/behavior.md`).
- Documentation in `docs/behavior.md`, `docs/configuration.md`, `README.md`, `.env.example` and `docs/install_application.md`, and a
  test for each case.
