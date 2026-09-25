"""
Downlink daemon: runs on the server (deb-serv-incus), computes real Moon
az/alt on a fixed cadence, and pushes it to the Pi's bridge.py over
Reticulum. bridge.py then forwards it to the gateway over serial, which
relays it to the field node over LoRa.

This is the server-side half of the downlink path:

    moon_calc.py -> RNS packet -> bridge.py (IN dest) -> serial "SET:az,alt"
    -> gateway firmware -> LoRa STEPPER_CONTROL -> field node

Mount limits: the rig's elevation actuator is hard-limited to 0-90 degrees
and its azimuth actuator sweeps only a 180 degree arc (not a full circle).
The Moon is below the horizon roughly half of every ~24h50m cycle, and for
part of that its azimuth also falls outside the reachable 180 degree arc.
Below the horizon, rather than sending an unreachable negative-altitude
target, this daemon "co-planar parks": it holds altitude at 0 and keeps
azimuth tracking the Moon's true (computed) azimuth whenever that azimuth
is within the mount's reachable arc, so the mount is already aimed at the
correct vertical plane the instant the Moon rises. When the true azimuth
falls outside the reachable arc, tracking is not possible at all (a
mechanical blind arc) and the mount is held at the nearest limit instead.

Usage:
    python3 moon_downlink_daemon.py --lat 49.123 --lon -123.456 \\
        --bridge-dest <bridge IN destination hash hex> [--interval 30]
"""

import argparse
import json
import time

import RNS

from moon_calc import moon_az_alt

APP_NAME = "moontracer"
ASPECT = "downlink"

ALT_MIN = 0.0
ALT_MAX = 90.0
# Horizon hysteresis: don't resume elevation tracking until the Moon is
# this far above the horizon, to avoid chatter from ephemeris/refraction
# jitter right at alt=0.
ALT_RESUME_DEADBAND = 0.5

# Send-suppression: don't re-issue a SET unless the target has moved at
# least this much, to avoid spamming serial/LoRa with near-identical
# commands every cadence tick.
MIN_DELTA_DEG = 0.05


def clamp_target(az, alt, az_min, az_max):
    """Map a computed (az, alt) to a commandable target given mount limits.

    Returns (target_az, target_alt, reachable) where reachable is False
    when az falls outside the mount's azimuth arc (a mechanical blind
    spot the mount cannot be pre-positioned for at any altitude).
    """
    reachable = az_min <= az <= az_max

    if not reachable:
        # Outside the sweepable arc entirely - hold at the nearer limit,
        # pinned to the horizon. Can't co-planar park on an azimuth the
        # mount physically cannot reach.
        target_az = az_min if abs(az - az_min) < abs(az - az_max) else az_max
        return target_az, ALT_MIN, False

    if alt < ALT_RESUME_DEADBAND:
        # Below horizon (or in the resume deadband): co-planar park.
        # Hold elevation at the floor, keep azimuth tracking the real
        # target so the mount is already aimed at moonrise.
        return az, ALT_MIN, True

    target_alt = min(max(alt, ALT_MIN), ALT_MAX)
    return az, target_alt, True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lat", type=float, required=True, help="Observer latitude, degrees (+N)")
    parser.add_argument("--lon", type=float, required=True, help="Observer longitude, degrees (+E)")
    parser.add_argument("--elevation", type=float, default=0.0, help="Observer elevation, meters")
    parser.add_argument("--bridge-dest", required=True, help="bridge.py downlink IN destination hash, hex")
    parser.add_argument("--interval", type=float, default=30.0, help="Cadence in seconds (10-60 typical)")
    parser.add_argument("--az-min", type=float, default=0.0, help="Mount azimuth arc lower bound, degrees")
    parser.add_argument("--az-max", type=float, default=180.0, help="Mount azimuth arc upper bound, degrees")
    args = parser.parse_args()

    reticulum = RNS.Reticulum()

    identity_path = RNS.Reticulum.storagepath + "/moon_downlink_identity"
    try:
        my_identity = RNS.Identity.from_file(identity_path)
        if my_identity is None:
            my_identity = RNS.Identity()
            my_identity.to_file(identity_path)
    except Exception:
        my_identity = RNS.Identity()
        my_identity.to_file(identity_path)

    dest_hash = bytes.fromhex(args.bridge_dest)
    if not RNS.Transport.has_path(dest_hash):
        print("No path to bridge yet, requesting...")
        RNS.Transport.request_path(dest_hash)
        time.sleep(3)

    bridge_identity = RNS.Identity.recall(dest_hash)
    if not bridge_identity:
        print("Could not recall bridge identity. Is bridge.py announced?")
        return

    bridge_dest = RNS.Destination(
        bridge_identity, RNS.Destination.OUT, RNS.Destination.SINGLE,
        APP_NAME, ASPECT
    )

    print(f"Downlink daemon started, lat={args.lat} lon={args.lon} interval={args.interval}s")

    last_sent = None
    while True:
        az, alt = moon_az_alt(args.lat, args.lon, args.elevation)
        target_az, target_alt, reachable = clamp_target(az, alt, args.az_min, args.az_max)

        if not reachable:
            print(f"Moon az={az:.2f} outside reachable arc [{args.az_min},{args.az_max}] - "
                  f"holding at nearest limit, true alt={alt:.2f}")

        if (
            last_sent is None
            or abs(target_az - last_sent[0]) >= MIN_DELTA_DEG
            or abs(target_alt - last_sent[1]) >= MIN_DELTA_DEG
        ):
            payload = json.dumps({"az": round(target_az, 3), "alt": round(target_alt, 3)})
            RNS.Packet(bridge_dest, payload.encode("utf-8")).send()
            print(f"Sent target az={target_az:.3f} alt={target_alt:.3f} "
                  f"(true az={az:.3f} alt={alt:.3f})")
            last_sent = (target_az, target_alt)

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
