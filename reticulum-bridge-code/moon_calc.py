"""
Moon position calculator for the tracker downlink.

Computes the Moon's topocentric azimuth/altitude for a given lat/lon using
pyephem (self-contained analytic ephemeris - no external data file download,
which matters for a server/Pi deployment that may not have internet at
runtime).

Usage:
    python3 moon_calc.py --lat 49.123 --lon -123.456 [--elevation 50]

Import usage (used by moon_downlink_daemon.py):
    from moon_calc import moon_az_alt
    az, alt = moon_az_alt(lat, lon, elevation_m)
"""

import argparse
import json
import math

import ephem


def moon_az_alt(lat, lon, elevation_m=0.0):
    """Return (azimuth_deg, altitude_deg) of the Moon right now.

    azimuth is 0-360 (0=N, 90=E). altitude is -90..90, negative when the
    Moon is below the horizon.
    """
    obs = ephem.Observer()
    obs.lat = str(lat)
    obs.lon = str(lon)
    obs.elevation = elevation_m
    obs.date = ephem.now()

    moon = ephem.Moon()
    moon.compute(obs)

    return math.degrees(moon.az), math.degrees(moon.alt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lat", type=float, required=True, help="Observer latitude, degrees (+N)")
    parser.add_argument("--lon", type=float, required=True, help="Observer longitude, degrees (+E)")
    parser.add_argument("--elevation", type=float, default=0.0, help="Observer elevation, meters")
    args = parser.parse_args()

    az, alt = moon_az_alt(args.lat, args.lon, args.elevation)
    print(json.dumps({"az": round(az, 3), "alt": round(alt, 3)}))


if __name__ == "__main__":
    main()
