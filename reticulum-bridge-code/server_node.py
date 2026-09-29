"""
Server-side Reticulum node for deb-serv-incus.

This is the production replacement for the bench-testing stand-in
(hello.py running on the Mac): a non-interactive, long-running uplink
receiver that logs every sensor reading forwarded by bridge.py to both
the console and a CSV file.

Run this continuously (e.g. under systemd - see docs/downlink_setup.md).
Because Reticulum instances on one host share a single local transport
(see the "shared instance" behavior documented after tonight's bench
testing), this should be the first Reticulum process started on the
server - moon_downlink_daemon.py, if run on the same host, will then
join this process's instance as a client instead of each spinning up
its own.

Usage:
    python3 server_node.py [--log data/uplink_log.csv]

On first run it prints its destination hash - copy that into
reticulum-bridge-code/bridge.py's PEER_HASH_HEX (uplink OUT destination)
so the Pi's bridge forwards sensor data here instead of to the Mac's
hello.py.
"""

import argparse
import csv
import os
import time

import RNS

APP_NAME = "helloworld"
ASPECT = "node"

DEFAULT_LOG_PATH = os.path.join(os.path.dirname(__file__), "data", "uplink_log.csv")
CSV_FIELDS = ["timestamp", "source", "values"]


def ensure_log_file(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", newline="") as f:
            csv.writer(f).writerow(CSV_FIELDS)


def make_callback(log_path):
    def received(message, packet):
        text = message.decode("utf-8", errors="replace")
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] [RECEIVED] {text}")

        try:
            import json
            data = json.loads(text)
            source = data.get("source")
            values = data.get("values")
        except (ValueError, AttributeError):
            source, values = None, text

        with open(log_path, "a", newline="") as f:
            csv.writer(f).writerow([ts, source, values])

    return received


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", default=DEFAULT_LOG_PATH, help="CSV log path")
    args = parser.parse_args()

    ensure_log_file(args.log)

    reticulum = RNS.Reticulum()

    identity_path = RNS.Reticulum.storagepath + "/server_node_identity"
    ratchet_path = RNS.Reticulum.storagepath + "/server_node_ratchets"
    try:
        my_identity = RNS.Identity.from_file(identity_path)
        if my_identity is None:
            my_identity = RNS.Identity()
            my_identity.to_file(identity_path)
    except Exception:
        my_identity = RNS.Identity()
        my_identity.to_file(identity_path)

    my_dest = RNS.Destination(
        my_identity, RNS.Destination.IN, RNS.Destination.SINGLE,
        APP_NAME, ASPECT
    )
    my_dest.set_proof_strategy(RNS.Destination.PROVE_ALL)
    my_dest.set_packet_callback(make_callback(args.log))
    my_dest.enable_ratchets(ratchet_path)
    my_dest.announce()

    print(f"Server uplink destination: {RNS.prettyhexrep(my_dest.hash)}")
    print(f"Copy this into reticulum-bridge-code/bridge.py's PEER_HASH_HEX on the Pi.")
    print(f"Logging received data to {args.log}")
    print("Listening for uplink data. Ctrl-C to quit.")

    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
