"""
Server-side live monitor for LoRa range test results.

Receives range-test packets forwarded over Reticulum by
lora-tracer-code/tools/range_test.py (run with --reticulum-dest pointed
at this script's destination hash), shows a live status line, and logs
every result to CSV — the same view range_test.py gives locally on the
Pi, but watchable from deb-serv-incus without needing a terminal on the
Pi at all.

Like server_node.py, this should be one of the first Reticulum processes
started on the host so other processes (moon_downlink_daemon.py, etc.)
join its shared instance rather than each spinning up their own.

Usage:
    python3 range_test_monitor.py [--log data/range_test_log.csv]

On first run it prints its destination hash - pass that to range_test.py
via --reticulum-dest on the Pi.
"""

import argparse
import csv
import json
import os
import sys
import time

import RNS

APP_NAME = "moontracer"
ASPECT = "rangetest"

DEFAULT_LOG_PATH = os.path.join(os.path.dirname(__file__), "data", "range_test_log.csv")
CSV_FIELDS = ["timestamp", "seq", "status", "rtt_ms", "rssi_local", "snr_local", "rssi_remote", "snr_remote"]


def ensure_log_file(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", newline="") as f:
            csv.writer(f).writerow(CSV_FIELDS)


class Stats:
    def __init__(self):
        self.sent = 0
        self.received = 0
        self.rssi_local = []
        self.rssi_remote = []
        self.snr_local = []
        self.snr_remote = []

    def add_ok(self, rssi_l, snr_l, rssi_r, snr_r):
        self.received += 1
        self.rssi_local.append(rssi_l)
        self.rssi_remote.append(rssi_r)
        self.snr_local.append(snr_l)
        self.snr_remote.append(snr_r)

    def loss_pct(self):
        return 100.0 * (self.sent - self.received) / self.sent if self.sent else 0.0

    def status_line(self, seq):
        if not self.rssi_local:
            return f"seq={seq}  sent={self.sent} recv={self.received} loss={self.loss_pct():.1f}%  (no OK yet)"
        return (
            f"seq={seq}  sent={self.sent} recv={self.received} loss={self.loss_pct():.1f}%  "
            f"RSSI(local/remote)={self.rssi_local[-1]:.0f}/{self.rssi_remote[-1]:.0f} dBm  "
            f"SNR(local/remote)={self.snr_local[-1]:.1f}/{self.snr_remote[-1]:.1f} dB"
        )


def make_callback(log_path, stats):
    def received(message, packet):
        try:
            data = json.loads(message.decode("utf-8", errors="replace"))
        except ValueError:
            return

        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        seq = data.get("seq")
        status = data.get("status")

        stats.sent += 1
        if status == "ok":
            stats.add_ok(data["rssi_local"], data["snr_local"], data["rssi_remote"], data["snr_remote"])
            row = [ts, seq, "ok", data["rtt_ms"], data["rssi_local"], data["snr_local"], data["rssi_remote"], data["snr_remote"]]
        else:
            row = [ts, seq, "miss", "", "", "", "", ""]

        with open(log_path, "a", newline="") as f:
            csv.writer(f).writerow(row)

        sys.stdout.write("\r" + stats.status_line(seq) + "   ")
        sys.stdout.flush()

    return received


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--log", default=DEFAULT_LOG_PATH, help="CSV log path")
    args = parser.parse_args()

    ensure_log_file(args.log)

    reticulum = RNS.Reticulum()

    identity_path = RNS.Reticulum.storagepath + "/range_test_monitor_identity"
    ratchet_path = RNS.Reticulum.storagepath + "/range_test_monitor_ratchets"
    try:
        my_identity = RNS.Identity.from_file(identity_path)
        if my_identity is None:
            my_identity = RNS.Identity()
            my_identity.to_file(identity_path)
    except Exception:
        my_identity = RNS.Identity()
        my_identity.to_file(identity_path)

    stats = Stats()

    my_dest = RNS.Destination(
        my_identity, RNS.Destination.IN, RNS.Destination.SINGLE,
        APP_NAME, ASPECT
    )
    my_dest.set_proof_strategy(RNS.Destination.PROVE_ALL)
    my_dest.set_packet_callback(make_callback(args.log, stats))
    my_dest.enable_ratchets(ratchet_path)
    my_dest.announce()

    print(f"Range test monitor destination: {RNS.prettyhexrep(my_dest.hash)}")
    print("Pass this to range_test.py on the Pi via --reticulum-dest")
    print(f"Logging to {args.log}")
    print("Waiting for range test results. Ctrl-C to quit.\n")

    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
