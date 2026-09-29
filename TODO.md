# TODO

## Field node: real stepper driving

`setTarget(az, alt)` in `lora-tracer-code/src/main.cpp` is scaffolded but
stubbed — it clamps and converts az/alt to step counts, then hands off to
`moveStepperTo()`, which just logs. Address once the stepper hardware is
actually up and running:

- Pick the stepper driver IC/library (e.g. A4988/DRV8825/TMC2209 +
  AccelStepper, or similar) and assign step/dir/enable pins for both the
  azimuth and elevation axes.
- Build a homing routine (limit switches, or driver stall detection) that
  establishes a known zero position for each axis on boot.
- From homing + the actual gear/belt ratio, fill in the real values for:
  - `STEPS_PER_DEG_AZ` / `STEPS_PER_DEG_ALT`
  - `AZ_HOME_OFFSET_DEG` / `ALT_HOME_OFFSET_DEG`
  - `FIELD_AZ_MIN`/`MAX`, `FIELD_ALT_MIN`/`MAX` — the mount's *actual*
    calibrated travel. These are the authoritative limits; they may not
    exactly match the generic `AZ_MIN`/`AZ_MAX`/`ALT_MIN`/`ALT_MAX` the
    gateway uses as an upstream sanity filter.
- Implement `moveStepperTo()` to actually drive the motors (and decide
  whether moves block `loop()` or run non-blocking against a target, given
  the field node also needs to keep servicing LoRa frames).

## Downlink setup doc

`docs/downlink_setup.md` has open TODOs of its own:
- Install lat/lon are confirmed (`50.33805`, `-113.71220`); elevation is
  still not on file — fill in once known.
- `--az-min`/`--az-max` there are placeholders (0/180) — confirm against
  the actual built mount.

## Confirmed working (2026-09-25)

Full downlink path (server ephemeris -> Reticulum -> bridge -> gateway
serial -> LoRa STEPPER_CONTROL -> field node -> ACK back) verified
end-to-end on the bench. Two bugs found and fixed along the way: silent
send failures in `moon_downlink_daemon.py` (no path confirmation) and a
missing role gate in `handleFrame()` that let the gateway act on its own
RF self-echoed frames. See `docs/downlink_setup.md`'s "Verified working"
section for details.

## Range test: real bug found and fixed on hardware (2026-09-29)

First hardware run looked perfect (0% loss, RSSI -38/-38 dBm, SNR
13.5/13.5 dB) but was testing the gateway against *itself*: unplugging
the field node entirely still showed 0.0% loss over 7500+ pings, because
`RANGE_PING`/`RANGE_PONG` handling wasn't role-gated and the gateway was
answering its own RF-self-echoed ping. Fixed in `main.cpp` by ignoring
any ping/pong whose `sourceId` matches the receiver's own `NODE_ID`.

**Needs a fresh reflash of both boards and a re-run before trusting any
range test result** - this hasn't been verified on hardware yet. A
correct test should show real loss when the field node is unplugged and
only `RANGE OK` when it's genuinely present.

Also fixed: a cosmetic display bug (live status line flickered fake
transient loss% between each ping's send and resolution - only updates
on resolution now). Still open: an actual walk-away distance test, and
the Reticulum-monitoring path (`range_test_monitor.py` +
`range_test.py --reticulum-dest`) hasn't been tried at all yet.

## Written but not yet run (2026-09-29)

`deb-serv-incus` Reticulum node: `reticulum-bridge-code/server_node.py`
(production, non-interactive replacement for the bench `hello.py`, logs
to CSV) plus a systemd unit template in `reticulum-bridge-code/systemd/`.
Setup steps in `docs/downlink_setup.md` section 0. Needs to actually be
run on `deb-serv-incus`, and `bridge.py`'s `PEER_HASH_HEX` / the daemon's
`--bridge-dest` repointed at it from the Mac bench stand-ins.
`reticulum-bridge-code/range_test_monitor.py` (see above) is meant to run
alongside it there.
