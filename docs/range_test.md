# LoRa range test

Characterizes how the gateway↔field LoRa link degrades with distance:
round-trip time, and RSSI/SNR as measured by *both* ends (the link isn't
symmetric, so one side's numbers alone can be misleading).

## How it works

- The gateway firmware has a built-in ping/pong mode: `RANGE_PING` /
  `RANGE_PONG` frame types (0x06/0x07), not role-gated — whichever node
  gets pinged pongs back automatically, current field-node firmware
  already supports this with no reflash needed.
- The gateway drives the test via two serial commands from a host:
  `RANGE:START:<interval_ms>` and `RANGE:STOP`. While a test is active,
  the gateway's normal synthetic `STEPPER_CONTROL`/`DATA_REQUEST` traffic
  is paused so it doesn't collide with ping/pong frames.
- `lora-tracer-code/tools/range_test.py` talks to the gateway's serial
  port directly, starts the test, parses the `RANGE OK`/`RANGE MISS`
  lines the firmware prints, shows a live one-line status, and logs
  every result to a CSV file.

## Running a test

**Stop `bridge.py` first** — it holds `/dev/ttyACM0` open and only one
process can own the gateway's serial port at a time.

On the Pi (or wherever the gateway board is currently attached):
```
cd lora-tracer-code/tools
python3 range_test.py --port /dev/ttyACM0 --interval-ms 1000
```

Walk the field node away from the gateway (or vice versa) while it runs.
Ctrl-C stops the test early; it also accepts `--duration <seconds>` to
stop automatically. Example live output:

```
seq=42  sent=42 recv=40 loss=4.8%  RSSI(local/remote)=-85/-90 dBm  SNR(local/remote)=3.2/2.1 dB  avg_rtt=210ms
```

- `local` = what the gateway measured receiving the field's pong.
- `remote` = what the field node measured receiving the gateway's ping
  (embedded in the pong's payload) — this is the number that actually
  tells you how well the field node is hearing the gateway, which is the
  direction that matters for `STEPPER_CONTROL` delivery.

Results are logged to `lora-tracer-code/tools/range_test_logs/range_<timestamp>.csv`
(gitignored — these are per-run bench data, not something to commit)
with columns `timestamp, seq, status, rtt_ms, rssi_local, snr_local,
rssi_remote, snr_remote`.

When done, restart `bridge.py` to resume normal uplink/downlink operation.

## Watching a test remotely over Reticulum

Run `reticulum-bridge-code/range_test_monitor.py` wherever you want to
watch from (e.g. `deb-serv-incus`) — it's a live receiver, same status
line as the local tool, plus its own CSV log:
```
cd reticulum-bridge-code
python3 range_test_monitor.py
```
It prints its destination hash on startup. Pass that to `range_test.py`
on the Pi:
```
python3 range_test.py --port /dev/ttyACM0 --reticulum-dest <hash>
```
Forwarding is best-effort — if the monitor can't be reached, `range_test.py`
prints one warning and the local test continues exactly as it would
without `--reticulum-dest`; a monitoring hiccup never blocks or fails the
actual test.

## Confirmed working (2026-09-29)

First real-hardware run: 0% loss over the initial stretch of pings at
close bench range, RSSI -38/-38 dBm, SNR 13.5/13.5 dB both directions —
consistent with a strong, healthy link. (The live status line briefly
showed misleading transient loss% values in this first run — a display
bug in the script, now fixed; it only updates once a round trip has
actually resolved, not on every ping sent.) Actual walk-away distance
testing and the Reticulum-monitor path are still untried.

## Caveats

- Assumes exactly one field node is powered and in range. If a second
  field node (or the future wind node) is also on air, both will answer
  every ping and collide — there's no per-node addressing in the ping/pong
  frames yet. Don't range-test with more than one field-role board
  powered at a time until that's added.
- `--interval-ms` below a few hundred ms risks a ping colliding with the
  field node's own periodic frames if it also has other traffic queued;
  1000ms (the default) has been the safe starting point in bench testing.
