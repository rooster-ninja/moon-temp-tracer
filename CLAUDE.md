# moon-temp-tracer — Claude Code handoff

Alt-az mount that tracks the moon and logs sensor-plate temperatures. This file captures the state of the LoRa + Reticulum comms work so far.

## Repo

- Remote: `git@github.com:rooster-ninja/moon-temp-tracer.git`
- Mac (canonical working copy): `~/Library/CloudStorage/Dropbox/Docs/moon-temp-tracer`
- Pi clone: `~/moon-temp-tracer` (pull from GitHub; don't edit on the Pi)
- Layout:
  - `CAD/`, `hardware/`, `assets/` — Millstone-Differential Bridge sensor circuit (KiCad, Rev F), datasheets
  - `lora-tracer-code/` — PlatformIO firmware for all LoRa nodes (gateway + field roles)
  - `reticulum-bridge-code/` — `bridge.py` (Pi) and `hello.py` (test receiver)
  - `moon_calc.py` — SunCalc-style moon position / rise-set / phase calculator (not yet committed to the repo; add it, probably under `reticulum-bridge-code/` or a new `server/` folder)
- `.gitignore` at repo root excludes `lora-tracer-code/.pio/` and `.vscode/`

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
- Stubs still in place: `setTarget()` (just logs) and `readADCChannels()` (fake incrementing values). Gateway currently sends a synthetic `az += 1.0` STEPPER_CONTROL plus a DATA_REQUEST every 3 s.

## Firmware build notes / gotchas

- PlatformIO CLI, board `seeed_xiao_esp32s3`, framework arduino, `jgromes/RadioLib @ ^6.6.0`, `upload_speed = 115200`, `monitor_speed = 115200`.
- Flash + watch: `pio run -e field --target upload && pio device monitor --baud 115200` (use `-e gateway` on the Pi).
- ESP-IDF `log_i`/`log_e` macros go to UART0, not USB CDC, on this board. Use the `Serial.printf`-based `LOGI/LOGW/LOGE/LOGD` macros.
- One-time boot prints are usually missed because the monitor attaches after USB re-enumeration. Put anything you need to see in a repeating heartbeat.
- A leftover `pio device monitor` locks the port ("port is busy"). `ps aux | grep pio` and kill it.
- Uploads failed repeatedly on Mac port 1101 ("serial noise", "chip stopped responding") at every baud rate; switching to another port (2101) fixed it. Suspect the port, not the boards.
- The Pi's `~/.reticulum/config` still contains the old `[[RNode LoRa]]` interface from the RNode test; it is set `enabled = False` because it fought `bridge.py` for `/dev/ttyACM0`. Both boards now run custom firmware, not RNode.

## Reticulum bridge (reticulum-bridge-code/bridge.py)

- Runs on the Pi. Reads `/dev/ttyACM0`, picks out `RNS:` lines, sends each JSON payload as an `RNS.Packet` to a SINGLE destination (`APP_NAME="helloworld"`, `ASPECT="node"`).
- Identity persisted at `~/.reticulum/storage/bridge_identity`.
- `PEER_HASH_HEX` is still the Mac `hello.py` hash `d8560d80c4d1dbad237c08b95b711cc5`. Replace with the server's hash.
- Status: forwarding logic works ("Forwarded to Reticulum: {...}" every cycle), but nothing arrives because there is no transport between the Pi and a receiver yet.

Design decisions already made (keep):

- Stateless Packets to an encrypted SINGLE destination, not Links.
- Two delivery layers: Reticulum proof-of-delivery (Pi <-> server hop) plus the app-level ACK 0x04 echoing seq (full path incl. LoRa). Proof without ACK means the LoRa hop failed.
- Identity discovery by announce-on-boot plus Reticulum path caching; only app_name/aspect strings are shared constants.
- Ratchets enabled on receiving destinations (`enable_ratchets(...)`).

## Next steps

1. Stand up Reticulum on `deb-serv-incus`: `pip install --break-system-packages rns`, config with `enable_transport = True` and a `TCPServerInterface` on `0.0.0.0:4242`; run the listen-only `hello.py` and record its destination hash. (Instructions were drafted; not yet confirmed done.)
2. On the Pi add `[[TCP Client]]` `TCPClientInterface` targeting `100.85.82.6:4242`, update `PEER_HASH_HEX`, restart `bridge.py`, confirm `[RECEIVED]` on the server.
3. Downlink: give `bridge.py` an IN destination; server runs `moon_calc.py` logic and sends az/alt; bridge writes a serial command (e.g. `SET:<az>,<alt>\n`) to the gateway; gateway parses it and calls `sendStepperControl()` instead of the synthetic `az += 1.0`. Open question: real install lat/lon (the `moon_calc.py` docstring example is 50.349, -113.775 in Alberta, which may not be the site).
4. Replace stubs with hardware: TMC2209 step/dir motion task (plan: pin radio to core 0, motion to core 1 with a mutex-protected target), real I2C ADC reads.
5. Wind node (NODE_ID 0x03): Mini-C2A-RS232 ultrasonic sensor (Modbus RTU, 9600 baud, DC 9-30V) -> MAX3232 (SparkFun 3.3V breakout) -> `Serial1` on free XIAO pins -> new frame type (proposed 0x05 WIND_DATA: speed + direction floats).
6. Persist bridge as a systemd service on the Pi.

## Open issues to check

- In recent gateway logs, DATA_REQUEST and DATA_RESPONSE both show source 0x01, and the gateway logs "Received DATA_REQUEST". Either the Mac board is not built with `-D NODE_ID=0x02`, or both boards are running the gateway role. Also `handleFrame()` is not role-gated (a gateway will answer DATA_REQUEST). Verify the Mac board's env/NODE_ID and gate handlers by role.
- `seqCounter` in ACKs is a local counter on the field node, not an echo of a sequence number sent by the gateway. STEPPER_CONTROL needs a seq field for the ACK to mean anything end to end.
- `moon_calc.py`: `get_moon_position` contains dead parallax code (`if False`) and an unused refraction term; fine for testing, clean up before relying on it.

## Shelved hardware (context only)

- Waveshare SX1262 915M LoRa HAT (UART, closed Ebyte-style firmware, M0/M1 jumpers, A/B/C routing jumper). Only talks to an identical HAT; cannot run RNode; not confirmed compatible with Waveshare DTUs. Not used.
- Waveshare RP2350-PiZero and Pi Zero 2 W wind-node plans dropped in favor of standardizing on XIAO/Wio-SX1262.
- microReticulum on the ESP32 was evaluated and rejected for now (watchdog-reboot reports in long unattended runs).
