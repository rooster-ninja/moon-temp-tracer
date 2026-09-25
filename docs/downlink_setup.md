# Downlink setup (server → bridge → gateway → field node)

This covers bringing up the real Moon-tracking downlink path described in
`reticulum-bridge-code/`. It assumes the uplink path (field → gateway →
bridge → Reticulum) is already working, per the existing bridge/hello
scripts.

## Topology

```
deb-serv-incus                  Pi                      gateway ESP32        field ESP32
(moon_downlink_daemon.py)  ---> (bridge.py)   --serial-->  (ROLE_GATEWAY)  --LoRa-->  (field, NODE_ID=0x02)
       |                    Reticulum              SET:az,alt        STEPPER_CONTROL        |
   moon_calc.py                (IN dest:                                              setTarget(az,alt)
   (pyephem, no                 "moontracer"/                                         -> stepper motion
   internet needed)             "downlink")
```

- `moon_calc.py` / `moon_downlink_daemon.py` run on the server and never
  touch the LoRa link directly — they only know Reticulum.
- `bridge.py` runs on the Pi. It has two Reticulum destinations now:
  - **OUT** to the test peer (`hello.py`) — unrelated to downlink, this is
    the uplink test path.
  - **IN**, app `moontracer` / aspect `downlink` — this is what the server
    addresses. Packets received here get turned into `SET:<az>,<alt>\n`
    on the gateway serial link.
- The gateway ESP32 (`ROLE_GATEWAY` build) reads `SET:` lines off serial
  and relays them to the field node as `STEPPER_CONTROL` LoRa frames.

## 1. Install dependencies

**On the server (deb-serv-incus):**
```
pip install ephem rns
```
`ephem` (pyephem) computes Moon position from a built-in analytic model —
no ephemeris file download required, so this works without internet
access at runtime.

**On the Pi:**
```
pip install rns pyserial
```
(`rns` should already be installed if the uplink path is working.)

## 2. Start the bridge and capture its downlink destination hash

```
cd reticulum-bridge-code
python3 bridge.py
```

On startup it now prints something like:
```
Downlink destination: <32 hex chars>
```

**Write this hash down** — it's the value the server needs as
`--bridge-dest`. It's derived from the Pi's Reticulum identity file
(`~/.reticulum/bridge_identity` by default), so it stays stable across
restarts as long as that identity file isn't deleted. Only regenerate/note
a new hash if that file is lost or the Pi is re-provisioned.

## 3. Required input values

These are the values the server-side daemon needs. None of them ship with
a default — `moon_downlink_daemon.py` refuses to run without `--lat`,
`--lon`, and `--bridge-dest` explicitly given, so a bad guess can't
silently get baked in.

| Value | Flag | Where it comes from | Notes |
|---|---|---|---|
| Latitude | `--lat` | Tracker's physical install site | Degrees, +N. **`50.33805`** (confirmed install coordinates). |
| Longitude | `--lon` | Tracker's physical install site | Degrees, +E (so west of Greenwich is negative). **`-113.71220`**. |
| Elevation | `--elevation` | Tracker's physical install site | Meters above sea level. Optional, defaults to 0 — affects horizon dip by a small, usually negligible amount unless the site is at real altitude. **Not yet on file** — bench testing so far has used the default (0). |
| Bridge destination hash | `--bridge-dest` | Printed by `bridge.py` on startup (step 2) | 32 hex chars. Changes only if the Pi's Reticulum identity file changes. |
| Cadence | `--interval` | Design choice | Seconds between recomputes/sends, 10–60 typical. Default 30. |
| Azimuth arc lower bound | `--az-min` | Mount's physical build | Degrees. Default 0. |
| Azimuth arc upper bound | `--az-max` | Mount's physical build | Degrees. Default 180 — **update this if the actual mount sweeps a different arc.** |

Elevation clamp (0–90°) is not a flag — it's fixed, since 0–90 is the
physical floor/ceiling of any alt-az elevation axis, not something that
varies per build.

## 4. Run the downlink daemon

```
cd reticulum-bridge-code
python3 moon_downlink_daemon.py \
  --lat 50.33805 \
  --lon -113.71220 \
  --bridge-dest <hash from step 2> \
  --interval 30
```

It will:
- Compute the Moon's real az/alt every `--interval` seconds.
- Clamp to the mount's reachable envelope, "co-planar parking" (azimuth
  tracks the true target, elevation pinned to 0°) whenever the Moon is
  below the horizon but azimuth is still reachable — see the comments at
  the top of `moon_downlink_daemon.py` for the below-horizon behavior in
  detail.
- Only send a new `SET` when the target has moved enough to matter
  (0.05°), to avoid spamming serial/LoRa every cadence tick with
  near-identical values.
- Actively poll for a Reticulum path (up to ~10s) before sending, and
  print `delivery CONFIRMED by bridge (rtt=...)` or `delivery NOT
  confirmed (timed out)` for every send, using a real proof-based receipt
  from `bridge.py`'s downlink destination (`PROVE_ALL`) — this is the only
  reliable way to know a target actually arrived, since `send()` not
  raising only means it was handed to an interface, not that anything
  received it.

## Verified working (2026-09-25 bench test)

Full path confirmed end-to-end on the bench, Pi gateway + Mac-tethered
field node, Mac standing in for the not-yet-provisioned `deb-serv-incus`:

```
moon_calc.py (real Moon az/alt, lat/lon above)
  -> Reticulum (daemon -> bridge.py, delivery-confirmed, rtt ~0.17-0.4s)
  -> gateway serial ("SET:180.00,0.00")
  -> LoRa STEPPER_CONTROL
  -> field node (setTarget -> moveStepperTo stub)
  -> LoRa ACK
  -> gateway (Received ACK from 0x02)
```

This also surfaced and fixed two real bugs along the way (see repo history
on `main`):
- `moon_downlink_daemon.py` sent regardless of whether a Reticulum path
  had actually resolved, so failed sends were completely silent on both
  ends - it now polls `has_path()` and confirms delivery via a packet
  receipt instead of assuming `send()` succeeding means anything arrived.
- `handleFrame()` in `lora-tracer-code/src/main.cpp` wasn't role-gated, so
  the gateway would hear its own transmitted `STEPPER_CONTROL`/
  `DATA_REQUEST` bounce back (RF self-reception) and process it as if it
  were real peer traffic - this was consistently winning the gateway's
  receive slot before the field node's real (slower, real-propagation)
  reply could land. Each role now only acts on the frame types the other
  role legitimately sends.

## 5. Firmware build flags (unchanged, for reference)

```
# platformio.ini
[env:gateway]
build_flags = -D ROLE_GATEWAY -D NODE_ID=0x01

[env:field]
build_flags = -D NODE_ID=0x02
```

## Still open

- Install elevation (meters) is not yet on file — lat/lon are confirmed
  above, elevation still needs a real value (bench testing has used the
  default of 0).
- `--az-min`/`--az-max` here are placeholders (0/180) matching a
  half-circle azimuth arc — confirm against the actual built mount before
  relying on them, and update this doc if the real arc differs.
- Field-node stepper calibration (steps/degree, home offset, real
  mechanical limits) is a separate concern from this doc — see the
  discussion in the project thread about az/alt vs. raw step commands on
  the wire. The field node owns that calibration; this daemon only ever
  deals in az/alt degrees.
