# moon_temp_tracer — Bridge Sensor Circuit Notes

Schematic: `bridge_circuit_schematic.html` (open in a browser, or see the published
Artifact). This document covers the reasoning behind it — the values, the tradeoffs, and
open questions for the next iteration.

## Why a bridge

`moon_temp_ads1115` uses a single-ended divider (3.3V → 10kΩ fixed → ADS1115 → NTC →
GND), giving ~55 m°C/count. Switching to a Wheatstone half-bridge on the ADS1115's
differential inputs gets three things at once: much finer resolution near the balance
point, rejection of common-mode noise/lead-resistance error on the signal path (both
sides of the differential pair pick up the same noise, which cancels), and a
ratiometric relationship to the excitation voltage rather than an absolute one.

## Topology (per sensor unit)

```
Vexc (LM4040, 2.048V) ─┬───────────────────────────── AIN0 (rail monitor, single-ended)
                        │
                        ├─ R1 (10kΩ) ─┬─ AIN1 (AINP)
                        │             R2 (10kΩ) ── GND
                        │
                        └─ R3 (10kΩ) ─┬─ AIN3 (AINN)
                                      RT1 (NTC, remote on steel bar) ── GND

SWITCHED_3V3 ── R4 (1.2kΩ) ── Vexc node ── LM4040-2.048 (shunt) ── GND
                (+ C1 10µF / C2 100nF ceramic X7R decoupling at Vexc node)
```

`SWITCHED_3V3` (not the always-on rail) feeds R4 and the ADS1115's VDD — see
**Power-gating** below.

AIN0 is wired straight to the Vexc bus — the same net as the top of R1 and R3 — so it
reads the excitation/rail voltage directly, not a divided-down value. That pins the
bridge's differential pair to **AIN1−AIN3**: the ADS1115 only supports four fixed
differential pairings (AIN0-AIN1, AIN0-AIN3, AIN1-AIN3, AIN2-AIN3), and with AIN0
committed to the rail, AIN1-AIN3 is the only valid pairing left for the bridge itself.
AIN2 is unused.

Reads per cycle: differential AIN1−AIN3 (bridge output, MUX 010, PGA ±2.048V) and
single-ended AIN0 (Vexc/rail, MUX 100, PGA ±4.096V — extra headroom above the 2.048V
nominal since this channel isn't the one that needs maximum resolution).

## Component values and why

| Part | Value | Rationale |
|---|---|---|
| U1, U2 | **ADS1115IDGSR** (TI, VSSOP-10) | Standard industrial grade, not the AEC-Q100 automotive `ADS1115BQDGSRQ1` variant — same core spec/data rate per TI's own pages; automotive qualification buys nothing here and typically costs more / has tighter sourcing. |
| R1, R2, R3 | **RNCF0603TKY10K0** (Stackpole), 10.0kΩ, 0.01%, ±5ppm/°C | Matched reference divider (R1/R2) plus the sense-leg fixed resistor (R3), which has **no partner to cancel against** — its own drift is direct sensor error, so it gets the same spec. This part comfortably exceeds the 0.1%/≤25ppm/°C this design originally called for. |
| RT1, RT2 | **B57861S0103F045** (TDK/EPCOS), 10kΩ NTC, B25/100=3988K ±0.3% | Epoxy bead, PTFE-insulated 50mm leads — good fit for thermal-epoxy mounting to the steel bar. Resistance tolerance ±1% only affects absolute matching between the two sensor units, not the bridge math (each unit gets its own empirical offset calibration anyway). |
| U3, U4 | LM4040AEM3-2.048, A-grade | 2.048V variant lines up with the ADS1115's own ±2.048V PGA setting. A-grade (~±15ppm/°C) matters here because its drift scales the *entire* bridge output proportionally — it's effectively another "reference resistor" from a tempco standpoint. |
| R4 | 1.2kΩ | Bias resistor for the LM4040 shunt. From SWITCHED_3V3 this gives ≈1.04mA; after the bridge draws its own 100-245µA (temperature-dependent), ≥800µA still flows through the LM4040 — comfortably above its ~60µA minimum bias spec across the whole range. |
| C1, C2 | 10µF / 100nF, **ceramic, X7R** | Bulk + high-frequency decoupling at the Vexc node. X7R specifically because it stays stable across -55°C to +125°C — better cold-temperature behavior than tantalum/electrolytic, which degrade (capacitance drop, rising ESR) below about -25°C. Put C1 in an 0805-or-larger case rated ≥10V to keep DC-bias capacitance derating modest at the 2.048V bias point. |
| R5, R6 | 4.7kΩ | I²C pull-ups — placed once, at the ESP32 end only (cable run is <1m, so 4.7k is fine at 400kHz; no reason to duplicate on both sensor boards). Stay on the ESP32's always-on 3.3V, not the switched branch, so the bus pull-ups remain valid even while the sensor branch is powered down. |
| Q1 | P-channel MOSFET, e.g. DMG2305UX / AO3401A (SOT-23) | High-side switch gating `SWITCHED_3V3`. Placeholder recommendation — exact part TBD alongside the rest of BOM sourcing, current draw here is trivial (a few mA at most). |
| R7 | ~10kΩ | Q1 gate pull-up to 3.3V — keeps the sensor branch OFF by default during ESP32 boot/reset when the GPIO state is undefined. |

## Why LM4040 instead of just measuring the raw rail

The user's original plan was to monitor the shared rail directly and rely on ratiometric
math to cancel drift. That still works for *slow* drift (regulator aging, temperature-
related shift) since Vexc is re-measured every cycle. It does **not** cancel fast noise —
if the bridge is read at a slightly different instant than the rail, any ripple or
transient between those two reads shows up as error. The shared 3.3V rail also feeds the
ESP32/WiFi radio, which draws bursty current during TX — exactly the kind of transient
that a separate-in-time rail read won't catch. A dedicated LM4040, isolated from that
rail by R4 + local decoupling, gives a quiet excitation node instead — still monitored
via AIN0 for full ratiometric math, just with a much lower noise floor to start from.

## Worked numbers (B57861S0103F045, B=3988K, -30°C to 45°C)

NTC resistance is highly non-linear over this range: **~4.3kΩ at 45°C → ~206kΩ at
-30°C** (NTC resistance rises steeply as it gets colder). With R3=10kΩ (bridge nulled
at 25°C, matching the existing project's convention):

| Temp | R_NTC | V_diff (AIN1−AIN3) |
|---|---|---|
| -30°C | ≈206kΩ | ≈ -0.93V |
| 25°C | 10kΩ | 0V (balanced) |
| 45°C | ≈4.3kΩ | ≈ +0.41V |

(Barely changed from the earlier MF58/B=3950K numbers — the two Beta values are close
enough that it doesn't move the PGA/headroom conclusion below.)

**PGA ±2.048V** (config code `010`) gives ~2.2× headroom over the -30°C extreme,
avoiding clipping even with thermistor tolerance and self-heating factored in.
Resolution at that PGA is 62.5µV/LSB; near 25°C that's roughly **~328 counts/°C** — about
18× finer than the existing single-ended design's ~55 m°C/count. Sensitivity tapers
toward the extremes since both the NTC and the bridge itself are non-linear there, but
stays usable across the full range.

Because sensitivity varies across the range, temperature must be recovered by inverting
the actual bridge equation for R_NTC from V_diff (not a linear approximation of the
bridge), then feeding that into the existing Beta equation — consistent with how
`Moon_Temp_Calibration_Jupyter` already treats the NTC's own non-linearity.

## Addressing (confirmed against user's plan)

| Sensor | ADS1115 ADDR pin | I²C address |
|---|---|---|
| Unit 1 | GND | 0x48 (same as existing `moon_temp_ads1115`) |
| Unit 2 | VDD | 0x49 |

Both share the ESP32's existing I2C bus (GPIO21=SDA, GPIO20=SCL). SDA/SCL address
options (0x4A/0x4B) are still free for future expansion.

## Power-gating

Both sensor boards are only powered during the brief read window, not continuously.
`SWITCHED_3V3` is a branch off the main 3.3V rail, gated by a single high-side P-channel
MOSFET (Q1) at the host end — one switch for both sensor cables, since they're already
read back-to-back in the same MQTT `SendData` cycle. The ESP32's own 3.3V supply is
upstream of Q1 and stays continuously powered; only the sensor branch gates.
`SWITCHED_3V3` feeds the ADS1115's VDD too, not just the LM4040/bridge chain — gating
everything together is simpler and avoids leaving the ADC partially powered for no
benefit. Q1's gate is pulled up to 3.3V through R7 (~10kΩ, default OFF at boot/reset)
and driven low by one new ESP32 GPIO to turn the branch on.

**Settling time**: R4 (1.2kΩ) + C1 (10µF) at the Vexc node form an RC with τ≈12ms.
Firmware needs to wait ≥5τ (~50-100ms) after enabling `SWITCHED_3V3` before triggering
the first ADS1115 conversion, so the LM4040 reference and bridge have actually settled.
Trivial against a 60s read interval.

**Does power-gating risk stray thermal effects from the circuitry itself?** The
opposite — it reduces them. Board draw when on is ≈4mW (LM4040 stage ≈3mW, bridge
dividers ≈0.3mW, ADS1115 ≈0.5mW). Continuous-on (any always-powered design) sustains
that 4mW 24/7; gated to a ~1-2s window every 60s (≈2-3% duty cycle) drops the average to
~100-130µW — a 30-40× reduction, and ~6mJ of energy per pulse is too little to
meaningfully move the local PCB temperature. The NTC itself isn't on this board (remote
on the steel bar via short flying leads), so board self-heating doesn't couple directly
into the sensing element regardless. Any residual bias would show up as a consistent,
repeatable offset tied to the read event, which the existing calibration workflow
already catches empirically.

**Electrical check**: with the branch fully de-energized, VDD/Vexc/all analog bridge
nodes sit at 0V together. SDA/SCL/ADDR digital pins on a powered-down ADS1115 are still
held at 3.3V by the host-side pull-ups (which stay on their own always-on 3.3V) — this
is within spec, since the datasheet's digital I/O absolute max is a fixed GND-0.3V to
5.5V range, not referenced to VDD like the analog inputs are. A powered-down ADS1115
also simply doesn't respond on the bus regardless of what its ADDR strap electrically
reads at that moment, so no addressing conflict either.

## Physical / layout notes

- Keep the entire bridge (R1-R3, LM4040, decoupling) on the small sensor PCB, right next
  to the ADS1115 — this is what makes the tempco spec above necessary (everything on the
  board rides the full outdoor swing), but it's still the right tradeoff since it
  minimizes lead-resistance mismatch and EMI pickup on the signal path itself.
- The NTC is **not** on this PCB — it's thermal-epoxied directly to the steel bar and
  connects back via short flying leads (`J1` in the schematic). Thermal epoxy is
  electrically non-conductive, and the PCB is additionally isolated from the bar with
  kapton tape layers, so there are two independent barriers against a short/ground path
  through the bar.
- I2C run is <1m for both units — standard 4.7kΩ pull-ups, 400kHz fast-mode is fine.
  Route SDA/SCL with a ground reference alongside (twisted pair even at this distance),
  since the board sits close to a WiFi radio and a switching regulator.

## Relationship to Moon_Temp_Overview

`Moon_Temp_Overview` documents a separate, related project — the gen-1 deployment
(`moon-temp-001`), a static rig using three single-ended channels (two moonlit reference
sensors averaged together, differenced in software against one shaded sensor). Its
circuit diagram asset is captioned "placeholder; official design files to follow," which
this schematic happens to visually supersede, but `moon_temp_tracer` is gen-2: different
hardware, mechanically tracking the moon via stepper motors, not a continuation or
replacement of the gen-1 rig. The channel-count/methodology choices from gen-1 (3
channels, software-averaged) are useful prior art but not a constraint this design needs
to match — the 2-unit bridge approach here is its own design, not a downgrade from gen-1.

## Deferred to later passes

- PCB layout/footprint selection — `CAD/s861.stp` and `CAD/RNCF0603TKY10K0.zip` are
  already staged for this, but actual layout is still a separate pass.
- Firmware changes in `moon_temp_ads1115`-style code: MUX/PGA config registers, the
  power-gate GPIO sequencing + settling delay, the non-linear bridge-inversion +
  Beta-equation (B=3988K) calculation, and the MQTT payload shape.
- Connector/cable choice for the I2C run, and enclosure/potting for outdoor exposure.
- Q1/R7 are placeholder part recommendations — exact selection can happen alongside the
  rest of BOM sourcing.
- Publishing this schematic + notes to rooster.ninja under skunk_werks/moon-temp-tracer
  — pending local approval first.
