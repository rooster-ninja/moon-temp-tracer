# moon_temp_tracer — Millstone-Differential Bridge Circuit Notes

**Millstone-Differential Bridge** — *"A Balanced Bridge Dimensionless Signal Detector"
(BBDSD)*. Schematic: `CAD/SENSOR_MoonTempTracer/SENSOR_MoonTempTracer.kicad_sch` (open in
KiCad — the earlier SVG-based schematic was abandoned in favor of a real KiCad file after
repeated label-collision problems; KiCad's native zoom/pan and Electrical Rules Check are
much stronger verification than a hand-drawn diagram). This document covers the reasoning
behind the design — the values, the tradeoffs, and the revision history.

## Why a bridge at all

`moon_temp_ads1115` (gen-1 sibling project) uses a single-ended divider (3.3V → 10kΩ
fixed → ADS1115 → NTC → GND), giving ~55 m°C/count. A Wheatstone-style differential
bridge gets much finer resolution near the balance point, rejects common-mode noise on
the signal path, and gives a ratiometric relationship to the excitation voltage instead
of an absolute one.

## Why *this* bridge: both thermistors as the active arms

The actual question this circuit needs to answer is narrower than "what's the absolute
temperature at each sensor" — it's "is there a difference between the moonlit and shaded
sensor, and which direction." Two independent half-bridges (each thermistor measured
against its own fixed reference) answer that only indirectly, by subtracting two
separately-erred absolute measurements in software. Putting **both thermistors as the
two active arms of one bridge** answers it directly, in hardware:

- **Direction is unambiguous everywhere in range.** NTC resistance rises monotonically as
  it gets colder, and bridge midpoint voltage rises monotonically with resistance
  (dV/dR > 0 always) — so sign(V_A − V_B) reliably tells you which sensor is colder
  across the whole -30°C to 45°C range, no sign reversal anywhere.
- **Common-mode rejection is structural, not statistical.** Vexc drift, ADC gain error,
  and the huge seasonal/diurnal swing itself are shared by both arms and cancel in the
  bridge output, instead of needing two independently-built boards to happen to agree.
- **"Dimensionless signal detector"**: the bridge's differential output is fundamentally
  a *ratio* — the relative mismatch between the two thermistor legs — not an absolute
  quantity. That's the core of the BBDSD framing.
- **Resolution is much better on the signal that matters.** A design with one leg frozen
  at a fixed reference needs huge PGA headroom to cover the full seasonal swing on the
  live leg. Here both thermistors track the same ambient swing together — only their
  (presumably small) difference shows up as V_diff — so a much tighter PGA works (see
  Worked Numbers below).

Given the sensors are only ~12" apart on the tracking rig, lead resistance at that
length is negligible (a few tens of mΩ) — no need to split fixed resistors out to each
remote location. Everything except the two bare thermistors lives on one central board.

## Topology (single central board)

```
Vexc (LM4040, 2.048V) ─┬─ Rf_A (10kΩ) ─┬─ mid_A ── (12" cable) ── RT_A (moonlit) ── GND
                        │               │
                        │               ├── AIN0 (single-ended → absolute T_A)
                        │               └── AIN1 (AINP → differential pair)
                        │
                        └─ Rf_B (10kΩ) ─┬─ mid_B ── (12" cable) ── RT_B (shaded) ── GND
                                        │
                                        ├── AIN2 (single-ended → absolute T_B)
                                        └── AIN3 (AINN → differential pair)

SWITCHED_VEXC ── R4 (1.2kΩ) ── Vexc node ── LM4040-2.048 (shunt) ── GND
                 (+ C1 10µF / C2 100nF ceramic X7R decoupling)
```

`mid_A` and `mid_B` each fan out to two ADS1115 pins (one for the single-ended absolute
read, one for its role in the differential pair) — a PCB routing detail, not an extra
component. One ADS1115, one LM4040 total.

**Reads per cycle** (single ADS1115, sequenced):
1. MUX 010, AIN1(+)/AIN3(−), PGA ±0.512V — primary differential signal
2. MUX 100, AIN0/GND, PGA ±4.096V — absolute T_A (needs full headroom, single leg swings
   the full range across -30..45°C on its own)
3. MUX 110, AIN2/GND, PGA ±4.096V — absolute T_B

No dedicated Vexc-monitor channel — all 4 AIN pins are committed to mid_A/mid_B in their
two roles. Both absolute channels are ratiometric to the same Vexc, so a drift would show
up as a correlated shift in both.

## Power domains (split — ADC always-on, bridge excitation gated)

Two separate rails, on purpose:

- **`+3V3`** — always-on, same rail the ESP32 and the I2C pull-ups already use. **U1
  (ADS1115) VDD lives here.** The ADC is never power-cycled.
- **`SWITCHED_VEXC`** — gated by Q1 (P-MOSFET), feeds *only* the bridge excitation path
  (R4 → LM4040 → Vexc → both bridge arms). R6 pulls Q1's gate to `+3V3` by default so the
  bridge stays OFF during ESP32 boot/reset when the GPIO state is undefined; a new ESP32
  GPIO (`GPIO_PWR_EN`, active-low) turns it on.

**Why split them**: with the ADC continuously powered, there's no ADC power-on/reference-
startup uncertainty mixed into the read timing. The only settling time left to account
for is the bridge's own RC time constant (R4 1.2kΩ + C1 10µF, τ≈12ms — wait ≥5τ, ~50-100ms,
before the first conversion after enabling `SWITCHED_VEXC`) — a single, precisely-known,
purely-bridge-limited quantity. Firmware can control exactly how long the bridge has been
energized before triggering a conversion, without that number being confounded by ADC
startup time. Minor tradeoff: the ADS1115 draws its own small quiescent current
continuously now instead of being power-cycled, but the chip already idles low-power
between triggered single-shot conversions even with VDD applied, so this is a small cost
for a real accuracy gain.

**Self-heating**: board draw when the bridge is on is ≈4mW (LM4040 stage ≈3mW, bridge
dividers ≈0.3mW). Gated to a ~1-2s window per 60s read (~2-3% duty) drops the average to
~100-130µW — a 30-40× reduction versus continuous-on. The thermistors are remote (not on
this board), so board self-heating doesn't couple directly into either reading regardless.

*(Earlier revision note: this board was originally going to gate the ADC too — one
switched rail for everything. Splitting the domains, motivated by wanting precise control
over bridge time-on for accuracy, is a Rev F change.)*

## Zeroing: digital offset, not a hardware trim

An earlier revision of this design included a physical multi-turn trim potentiometer
(POT1) plus a 1MΩ injection resistor (R7), wired to null the bridge to zero at a known
common temperature before deployment. **That hardware has been removed.** Zeroing is done
entirely digitally instead, using the same mechanism `moon_temp_ads1115` already has
proven in the field: an MQTT-settable per-channel offset, persisted to flash, added to
the raw reading before publishing (`mosquitto_pub ... offset/cmd`). No reflash needed to
adjust it.

**Calibration procedure**: with both thermistors sitting together at the same known
temperature (a shared environment), the digital offset is set so the bridge reading is
zero. After that, the calibrated signal should read as pure noise around zero — that's
the validation criterion. A real signal during an actual moonlight event is then judged
against that established noise floor, not against zero directly (same concept as the
gen-1 project's `Moon_Temp_Calibration_Jupyter` ±3 ADC count noise-floor
characterization).

**Honest limitation**: a single offset value nulls a *constant* mismatch (resistor
tolerance, lead asymmetry, ADC offset) at the calibration temperature. The two
thermistors' B-values are only matched to ±0.3% (B57861S0103F045 spec) — a perfect null
at one temperature can drift slightly at other ambient temperatures if the pair's R-vs-T
curves aren't identical. That residual isn't fixable with a single constant offset; it
needs the same empirical, multi-point calibration `Moon_Temp_Calibration_Jupyter` already
does for the existing design. Some drift over the sensor's lifetime is also expected
(thermistor aging) — periodic re-zeroing via the same MQTT command, no reflash required,
is the mitigation.

## Component values and why

| Part | Value | Rationale |
|---|---|---|
| U1 | **ADS1115IDGSR** (TI, VSSOP-10) | Standard industrial grade, not the AEC-Q100 automotive `ADS1115BQDGSRQ1` variant — same core spec/data rate per TI's own pages; automotive qualification buys nothing here. |
| Rf_A, Rf_B | **RNCF0603TKY10K0** (Stackpole), 10.0kΩ, 0.01%, ±5ppm/°C | Both bridge arms' fixed resistors. Their tempco matters more here than in a symmetric bridge — they sit at genuinely different temperatures from each other (that's the point of the experiment), so their own drift directly sets the false-offset floor, not just long-term absolute drift. |
| RT_A, RT_B | **B57861S0103F045** (TDK/EPCOS), 10kΩ NTC, B25/100=3988K ±0.3% | Epoxy bead, PTFE-insulated 50mm leads — good fit for thermal-epoxy mounting to the steel bar. ±1% resistance tolerance is handled by the digital offset calibration, not hardware matching. |
| U2 | LM4040AEM3-2.048, A-grade | 2.048V variant lines up with the ADS1115's own ±2.048V PGA setting. A-grade (~±15ppm/°C) matters because its drift scales the entire bridge output proportionally. |
| R4 | 1.2kΩ | Bias resistor for the LM4040 shunt, fed from `SWITCHED_VEXC`. Gives ≈1.04mA; after the bridge draws its own 100-245µA, ≥800µA still flows through the LM4040 — comfortably above its ~60µA minimum bias spec. |
| C1, C2 | 10µF / 100nF, ceramic, X7R | Bulk + high-frequency decoupling at the Vexc node. X7R stays stable across -55°C to +125°C — better cold-temperature behavior than tantalum/electrolytic, which degrade below about -25°C. |
| R5, R6(I2C) | 4.7kΩ | I²C pull-ups on the always-on `+3V3` — placed once, at the host end only. |
| Q1 | P-channel MOSFET, e.g. DMG2305UX | High-side switch gating `SWITCHED_VEXC`. |
| R6(gate) | ~10kΩ | Q1 gate pull-up to `+3V3` — keeps the bridge OFF by default during ESP32 boot/reset. |

*(KiCad reference designators for the two I2C pull-ups and the gate pull-up are R4/R5/R6
in the schematic file — see the file directly for the authoritative mapping; the table
above groups by function.)*

## Worked numbers (B57861S0103F045, B=3988K, -30°C to 45°C)

Wheatstone bridge sensitivity peaks at balance (both arms equal, ~25°C where RT≈Rf=10kΩ)
and tapers toward both extremes:

| Ambient baseline | Sensitivity (dV/dΔT) |
|---|---|
| -30°C | ~6.1 mV/°C (worst case) |
| 25°C | ~23.0 mV/°C (peak, bridge balanced) |
| 45°C | ~17.0 mV/°C |

NTC resistance ranges from ~4.3kΩ at 45°C to ~206kΩ at -30°C. With Rf=10kΩ (bridge
nulled digitally, not by hardware, at the calibration temperature):

| Temp | R_NTC | V_diff (AIN1−AIN3) |
|---|---|---|
| -30°C | ≈206kΩ | ≈ -0.93V |
| 25°C | 10kΩ | 0V (balanced) |
| 45°C | ≈4.3kΩ | ≈ +0.41V |

Even at the worst-case cold end, **PGA ±0.512V** covers a differential well beyond any
plausible real effect without clipping. Resolution at that PGA gives roughly
**~780-2900 counts/°C** depending on ambient baseline. Because sensitivity varies across
the range, temperature must be recovered by inverting the actual bridge equation for
R_NTC from V_diff (not a linear approximation), then feeding that into the Beta equation.

## Addressing

| Unit | ADS1115 ADDR pin | I²C address |
|---|---|---|
| U1 (only ADS1115 on this board) | GND | 0x48 |

Single ADS1115 on the ESP32's existing I2C bus (GPIO21=SDA, GPIO20=SCL). SDA/SCL/ADDR
address options (0x49/0x4A/0x4B) remain free for future expansion.

## Physical / layout notes

- Keep the entire bridge (Rf_A, Rf_B, LM4040, decoupling) on the central board, right
  next to the ADS1115 — minimizes lead-resistance mismatch and EMI pickup on the signal
  path. This is what makes the Rf_A/Rf_B tempco spec above necessary (everything on the
  board rides the full outdoor swing).
- The two NTCs are **not** on this board — each is thermal-epoxied directly to its own
  steel bar and connects back via ~12" flying leads (twisted pair recommended, though at
  this length lead resistance itself is negligible). Thermal epoxy is electrically
  non-conductive, and each PCB is additionally isolated from its bar with kapton tape
  layers.
- I2C run to the ESP32 host is <1m — standard 4.7kΩ pull-ups, 400kHz fast-mode is fine.

## Relationship to Moon_Temp_Overview

`Moon_Temp_Overview` documents a separate, related project — the gen-1 deployment
(`moon-temp-001`), a static rig using three single-ended channels (two moonlit reference
sensors averaged together, differenced in software against one shaded sensor).
`moon_temp_tracer` is gen-2: different hardware, mechanically tracking the moon via
stepper motors — not a continuation or replacement of the gen-1 rig, just related in
concept.

## Revision history

- **Rev D**: two independent half-bridges, two ADS1115 + two LM4040, published to
  rooster.ninja.
- **Rev E**: redesigned as a single centralized bridge with both thermistors as active
  arms — one ADS1115, one LM4040, physical POT1 null-trim, ADC and bridge excitation
  sharing one switched power rail. KiCad schematic built (SVG approach abandoned after
  repeated readability problems).
- **Rev F** (current): named **Millstone-Differential Bridge (BBDSD)**. Removed POT1/R7
  — zeroing is now entirely digital (MQTT-settable offset, same mechanism as
  `moon_temp_ads1115`). Split power domains — ADC on always-on `+3V3`, MOSFET gates only
  the bridge excitation (`SWITCHED_VEXC`) — for precise, ADC-startup-independent control
  over bridge time-on accuracy.

## Deferred to later passes

- PCB layout/footprint placement (KiCad project exists at `CAD/SENSOR_MoonTempTracer/`
  with placeholder symbols for parts not yet sourced with real footprints — LM4040, both
  thermistors, Q1, and the generic resistors).
- Firmware: MUX/PGA config registers, power-gate GPIO sequencing + settling delay,
  digital offset command handling (MQTT `offset/cmd`, flash persistence — direct reuse of
  the `moon_temp_ads1115` pattern), bridge-inversion + Beta-equation (B=3988K)
  calculation, MQTT payload shape.
- Connector/cable choice for the two ~12" thermistor runs, enclosure/potting for outdoor
  exposure.
- Multi-point empirical calibration (beyond the single-temperature digital zero) —
  characterizing residual offset-vs-baseline-temperature for the specific thermistor
  pair, same static-chamber approach as `Moon_Temp_Calibration_Jupyter`.
