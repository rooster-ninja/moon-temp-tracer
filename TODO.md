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

`docs/downlink_setup.md` has two open TODOs of its own:
- Real tracker install lat/lon/elevation aren't on file yet — fill in once
  known.
- `--az-min`/`--az-max` there are placeholders (0/180) — confirm against
  the actual built mount.
