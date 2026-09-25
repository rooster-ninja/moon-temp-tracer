# moon-temp-tracer — Claude Code handoff

Alt-az mount that tracks the moon and logs sensor-plate temperatures. This file captures the state of the LoRa + Reticulum comms work so far.

## Repo

- Remote: `git@github.com:rooster-ninja/moon-temp-tracer.git`
- Mac (canonical working copy): `~/Library/CloudStorage/Dropbox/Docs/moon-temp-tracer`
- Pi clone: `~/moon-temp-tracer` (pull from GitHub; don't edit on the Pi)
- Layout:
  - `CAD/`, `hardware/`, `assets/` — Millstone-Differential Bridge sensor circuit (KiCad, Rev F), datasheets
  - `lora-tracer-code/` — PlatformIO firmware for all LoRa nodes (gateway + field roles)
  - `reticulum-bridge-code/` — `bridge.py` (Pi), `hello.py` (test receiver), `moon_calc.py` (pyephem-based Moon az/alt) and `moon_downlink_daemon.py` (server-side downlink loop)
  - `docs/downlink_setup.md` — full downlink bring-up doc (topology, dependency installs, every required input value and where it comes from)
  - `TODO.md` — field-node stepper hardware TODOs, downlink doc TODOs
- `.gitignore` at repo root excludes `lora-tracer-code/.pio/`, `.vscode/`, and Python `__pycache__/`

## Topology (target)

```
Field node (XIAO ESP32S3 + Wio-SX1262, custom RadioLib fw, NODE_ID 0x02)
   <-- LoRa, private frame protocol -->
Gateway radio (XIAO ESP32S3 + Wio-SX1262, NODE_ID 0x01) --USB serial--> Raspberry Pi 3B
   Pi runs bridge.py: serial <-> Reticulum
   <-- Reticulum over TCP (Tailscale) -->
deb-serv-incus (Debian): ephemeris (moon_calc.py) + data logging
```

- The LoRa hop is deliberately NOT Reticulum. Reticulum runs only on the Pi and the Debian server.
- The Mac is only a bench host for the field-node board; it is not part of the deployed system.
- Planned wind node: third XIAO/Wio-SX1262 board, NODE_ID 0x03, same firmware pattern (see below).

## Hosts

| Host | Tailscale name | Tailscale IP | User | Notes |
|---|---|---|---|---|
| Gateway Pi 3B | `pi-lora-reticulum-node` | 100.124.42.74 | rooster | LAN 10.0.10.16, Raspberry Pi OS Lite 64-bit (Debian 13), gateway board on `/dev/ttyACM0` |
| Server | `deb-serv-incus` | 100.85.82.6 | rooster | Reticulum TCP server planned on port 4242 |
| Mac | `goose-compu-tron-6000` | 100.94.249.80 | dek | Field board on `/dev/cu.usbmodem2101` (port 1101 was flaky) |

- Tailscale SSH on the Pi was turned off (`tailscale set --ssh=false`); plain OpenSSH + mosh are used.
- Pi has `en_CA.UTF-8` and `en_US.UTF-8` locales generated (mosh needs them).
- Mac Reticulum venv: `~/reticulum-env` (recreated after a Homebrew Python upgrade broke it).

## Radio hardware: Seeed XIAO ESP32S3 + Wio-SX1262 kit (B2B connector)

Verified working pin map (the commonly posted 5/3/2/4 pins are for a different variant and give RadioLib error -2):

| Signal | GPIO |
|---|---|
| NSS/CS | 41 |
| DIO1 | 39 |
| RESET | 42 |
| BUSY | 40 |
| RF switch | 38 (drive HIGH) |
| SCK / MISO / MOSI | 7 / 8 / 9 (`SPI.begin(7, 8, 9, 41)`) |

Radio params: 915.0 MHz, BW 125 kHz, SF 8, CR 5, `RADIOLIB_SX126X_SYNC_WORD_PRIVATE`, 17 dBm. Bench RSSI about -6 to -15 dBm, SNR about 12 to 14 dB.

## Frame protocol (lora-tracer-code/src/main.cpp)

Every frame is exactly 20 bytes, zero-padded, sent and read as 20 bytes. Do not trust the radio's reported packet length; variable-length frames caused stale-length reads, garbage floats, and a stack-smashing crash.

`[0] type | [1] source NODE_ID | [2..] payload`

| Type | Name | Payload |
|---|---|---|
| 0x01 | STEPPER_CONTROL | float az @2, float alt @6 (absolute degrees) |
| 0x02 | DATA_REQUEST | none |
| 0x03 | DATA_RESPONSE | uint8 count @2 (clamped to 4), float[count] @3 |
| 0x04 | ACK | uint8 seq @2 |

- NODE_ID comes from build flags in `platformio.ini`: env `gateway` = `-D ROLE_GATEWAY -D NODE_ID=0x01`, env `field` = `-D NODE_ID=0x02`.
- RX is interrupt driven: `setDio1Action(setFlag)` + `startReceive()`, then `readData(buf, FRAME_SIZE)` in `loop()`. Do not use blocking `radio.receive()` (it starves the loop).
- Every send does `standby()`, `delay(10)`, `transmit()`, `startReceive()`.
- Gateway emits a machine-readable line on each DATA_RESPONSE for the bridge: `RNS:{"source":N,"values":[...]}`. Everything else on serial is human debug text (`[INFO]`, `[WARN]`, `[ERROR]`, `[DEBUG]`).
- `handleFrame()` is role-gated: `STEPPER_CONTROL`/`DATA_REQUEST` are only acted on by the field build, `DATA_RESPONSE`/`ACK` only by the gateway build. Fixed 2026-09-25 — previously ungated, so the gateway would hear its own transmitted frames bounce back (RF self-reception) and process them as if they were real peer traffic; a self-heard frame is now logged at DEBUG and ignored.
- Gateway parses `SET:<az>,<alt>\n` lines off serial (from `bridge.py`'s downlink callback) via `pollSerialCommands()`/`handleSerialLine()`, and relays them as real `STEPPER_CONTROL` frames — this replaced the old synthetic `az += 1.0` test loop. DATA_REQUEST polling (every 3s) is unchanged/still synthetic.
- `setTarget()` on the field node is scaffolded (clamps to `FIELD_AZ/ALT_MIN/MAX`, converts to step counts via `STEPS_PER_DEG_AZ/ALT` + home offsets) but `moveStepperTo()` is still a stub that only logs — see `TODO.md` for the real stepper-hardware work.
- `readADCChannels()` is still a stub (fake incrementing values) — real I2C ADC reads not yet wired up.

## Firmware build notes / gotchas

- PlatformIO CLI, board `seeed_xiao_esp32s3`, framework arduino, `jgromes/RadioLib @ ^6.6.0`, `upload_speed = 115200`, `monitor_speed = 115200`.
- Flash + watch: `pio run -e field --target upload && pio device monitor --baud 115200` (use `-e gateway` on the Pi).
- ESP-IDF `log_i`/`log_e` macros go to UART0, not USB CDC, on this board. Use the `Serial.printf`-based `LOGI/LOGW/LOGE/LOGD` macros.
- One-time boot prints are usually missed because the monitor attaches after USB re-enumeration. Put anything you need to see in a repeating heartbeat.
- A leftover `pio device monitor` locks the port ("port is busy"). `ps aux | grep pio` and kill it.
- Uploads failed repeatedly on Mac port 1101 ("serial noise", "chip stopped responding") at every baud rate; switching to another port (2101) fixed it. Suspect the port, not the boards.
- The Pi's `~/.reticulum/config` still contains the old `[[RNode LoRa]]` interface from the RNode test; it is set `enabled = False` because it fought `bridge.py` for `/dev/ttyACM0`. Both boards now run custom firmware, not RNode.

## Reticulum bridge (reticulum-bridge-code/bridge.py)

- Runs on the Pi. Reads `/dev/ttyACM0`, picks out `RNS:` lines, sends each JSON payload as an `RNS.Packet` to a SINGLE destination (`APP_NAME="helloworld"`, `ASPECT="node"`) — this is the uplink path.
- Also now has an **IN** destination (`APP_NAME="moontracer"`, `ASPECT="downlink"`, `PROVE_ALL`) for the downlink: packets received there get turned into `SET:<az>,<alt>\n` on the gateway serial link. Its hash is printed on startup (`Downlink destination: <32 hex>`) and is what `moon_downlink_daemon.py` needs as `--bridge-dest`.
- Identity persisted at `~/.reticulum/storage/bridge_identity` — both destination hashes are derived from it and stay stable across restarts as long as that file isn't deleted.
- `PEER_HASH_HEX` (uplink) is still the Mac `hello.py` hash `d8560d80c4d1dbad237c08b95b711cc5`. Replace with the server's hash once `deb-serv-incus` is up.
- Status: uplink forwarding confirmed working end-to-end (`[RECEIVED]` on a live Reticulum receiver). Downlink confirmed working end-to-end too (see "Downlink — confirmed working" below). `deb-serv-incus` itself is still not provisioned — bench testing used the Mac as a stand-in transport for both directions, over an explicit `[[TCP Server Interface]]`/`[[TCP Client Interface]]` pair in `~/.reticulum/config` on each side (LAN multicast auto-discovery was unreliable between the Pi and Mac).

## Downlink — confirmed working (2026-09-25)

Full path verified end-to-end on the bench (Mac standing in for `deb-serv-incus`):

```
moon_calc.py (real Moon az/alt, lat=50.33805 lon=-113.71220)
  -> Reticulum (moon_downlink_daemon.py -> bridge.py, delivery-confirmed via packet receipt, rtt ~0.17-0.4s)
  -> gateway serial ("SET:180.00,0.00")
  -> LoRa STEPPER_CONTROL
  -> field node (setTarget -> moveStepperTo stub)
  -> LoRa ACK
  -> gateway (Received ACK from 0x02)
```

Two real bugs were found and fixed while getting to this point:
- `moon_downlink_daemon.py` sent regardless of whether a Reticulum path had actually resolved (`Identity.recall()` succeeding only means the peer's public key is cached, not that a route exists), so failed sends were completely silent on both ends. It now polls `has_path()` for up to ~10s and confirms every send via a real proof-based packet receipt (`delivery CONFIRMED (rtt=...)` / `delivery NOT confirmed (timed out)`) instead of assuming `send()` not raising means anything arrived.
- The gateway/field role-gating bug below (now fixed) was actively blocking this test: the gateway's self-heard echo of its own `STEPPER_CONTROL` was winning its single receive slot before the field node's real (further, slower) reply could land.

See `docs/downlink_setup.md` for the full setup doc and `TODO.md` for what's still open (elevation, az-min/max confirmation, real stepper hardware).

Design decisions already made (keep):

- Stateless Packets to an encrypted SINGLE destination, not Links.
- Two delivery layers: Reticulum proof-of-delivery (Pi <-> server hop) plus the app-level ACK 0x04 echoing seq (full path incl. LoRa). Proof without ACK means the LoRa hop failed.
- Identity discovery by announce-on-boot plus Reticulum path caching; only app_name/aspect strings are shared constants.
- Ratchets enabled on receiving destinations (`enable_ratchets(...)`).

## Next steps

1. Stand up Reticulum on `deb-serv-incus` for real: `pip install ephem rns` (rns already needed by bridge.py; `ephem` is new for `moon_calc.py`), config with `enable_transport = True` and a `TCPServerInterface` on `0.0.0.0:4242`. Bench testing has proven the whole path works with the Mac standing in for this — swapping in the real server is now just a deployment step, not a design question.
2. On the Pi, point `bridge.py`'s uplink `PEER_HASH_HEX` and the daemon's `--bridge-dest` target at the real server once it's up (currently pointed at the Mac's `hello.py`/bench `moon_downlink_daemon.py` for testing). Add `[[TCP Client Interface]]` targeting `100.85.82.6:4242` on the Pi (mirroring the bench TCP interface pair used for Mac testing).
3. Fill in the tracker's real elevation (lat/lon are confirmed: `50.33805`, `-113.71220`) and confirm `--az-min`/`--az-max` (currently placeholder `0`/`180`) against the actual built mount — see `docs/downlink_setup.md`.
4. Replace stubs with hardware: TMC2209 step/dir motion task (plan: pin radio to core 0, motion to core 1 with a mutex-protected target), real I2C ADC reads, real `STEPS_PER_DEG_AZ/ALT` + home offsets in `main.cpp` (see `TODO.md`).
5. Wind node (NODE_ID 0x03): Mini-C2A-RS232 ultrasonic sensor (Modbus RTU, 9600 baud, DC 9-30V) -> MAX3232 (SparkFun 3.3V breakout) -> `Serial1` on free XIAO pins -> new frame type (proposed 0x05 WIND_DATA: speed + direction floats).
6. Persist bridge (and eventually the downlink daemon) as a systemd service on the Pi/server.

## Open issues to check

- `seqCounter` in ACKs is a local counter on the field node, not an echo of a sequence number sent by the gateway. STEPPER_CONTROL needs a seq field for the ACK to mean anything end to end.

## Shelved hardware (context only)

- Waveshare SX1262 915M LoRa HAT (UART, closed Ebyte-style firmware, M0/M1 jumpers, A/B/C routing jumper). Only talks to an identical HAT; cannot run RNode; not confirmed compatible with Waveshare DTUs. Not used.
- Waveshare RP2350-PiZero and Pi Zero 2 W wind-node plans dropped in favor of standardizing on XIAO/Wio-SX1262.
- microReticulum on the ESP32 was evaluated and rejected for now (watchdog-reboot reports in long unattended runs).
