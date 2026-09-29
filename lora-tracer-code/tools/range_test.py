"""
LoRa range test driver.

Talks directly to the GATEWAY board's serial port (bypassing bridge.py -
stop bridge.py first, only one process can hold the port) and drives the
firmware's built-in range-test mode: the gateway pings the field node at
a fixed interval and logs round-trip time plus RSSI/SNR as measured by
*both* ends (LoRa links aren't symmetric, so one-sided numbers can be
misleading). Any currently-flashed field node answers automatically -
no special mode needed on that side, and no addressing is done, so this
assumes a single field node is currently powered and in range (if more
than one field node is on air, don't run a range test - they will collide
answering the same ping).

Walk the field node away from the gateway (or vice versa) while this
runs to characterize how RSSI/SNR/loss degrade with distance/obstruction.

Usage:
    python3 range_test.py --port /dev/ttyACM0 [--interval-ms 1000] [--duration 120]

Optionally forward every result over Reticulum to a
range_test_monitor.py running elsewhere (e.g. deb-serv-incus), so the
test can be watched live without a terminal on whatever host the gateway
is attached to:

    python3 range_test.py --port /dev/ttyACM0 --reticulum-dest <hash>

Reticulum forwarding is best-effort and never blocks or fails the local
test - if the destination can't be reached, a warning is printed once and
the test continues logging locally exactly as it would without it.

Ctrl-C stops the test early and prints the same summary the firmware
would print at the end anyway.
"""

import argparse
import csv
import json
import os
import re
import sys
import time

import serial

PING_RE = re.compile(r"RANGE PING seq=(\d+)")
OK_RE = re.compile(
    r"RANGE OK seq=(\d+) rtt_ms=(\d+) "
    r"rssi_local=(-?[\d.]+) snr_local=(-?[\d.]+) "
    r"rssi_remote=(-?[\d.]+) snr_remote=(-?[\d.]+)"
)
MISS_RE = re.compile(r"RANGE MISS seq=(\d+)")
SUMMARY_RE = re.compile(r"RANGE SUMMARY sent=(\d+) received=(\d+) loss_pct=([\d.]+)")

DEFAULT_LOG_DIR = os.path.join(os.path.dirname(__file__), "range_test_logs")
CSV_FIELDS = ["timestamp", "seq", "status", "rtt_ms", "rssi_local", "snr_local", "rssi_remote", "snr_remote"]


def default_log_path():
    os.makedirs(DEFAULT_LOG_DIR, exist_ok=True)
    return os.path.join(DEFAULT_LOG_DIR, time.strftime("range_%Y%m%d_%H%M%S.csv"))


RETICULUM_APP_NAME = "moontracer"
RETICULUM_ASPECT = "rangetest"


def setup_reticulum_forwarder(dest_hash_hex):
    """Best-effort: returns an RNS OUT Destination to forward results to,
    or None if it can't be set up (never raises - a monitoring feature
    should never take down the actual test)."""
    try:
        import RNS
    except ImportError:
        print("WARNING: --reticulum-dest given but the 'rns' package isn't installed - forwarding disabled.")
        return None

    try:
        reticulum = RNS.Reticulum()

        identity_path = RNS.Reticulum.storagepath + "/range_test_sender_identity"
        try:
            my_identity = RNS.Identity.from_file(identity_path)
            if my_identity is None:
                my_identity = RNS.Identity()
                my_identity.to_file(identity_path)
        except Exception:
            my_identity = RNS.Identity()
            my_identity.to_file(identity_path)

        dest_hash = bytes.fromhex(dest_hash_hex)
        if not RNS.Transport.has_path(dest_hash):
            print("Reticulum: no path to monitor yet, requesting...")
            RNS.Transport.request_path(dest_hash)
            for _ in range(20):
                time.sleep(0.5)
                if RNS.Transport.has_path(dest_hash):
                    break

        monitor_identity = RNS.Identity.recall(dest_hash)
        if not monitor_identity:
            print("WARNING: could not recall range_test_monitor identity - is it running and announced? Forwarding disabled.")
            return None

        dest = RNS.Destination(
            monitor_identity, RNS.Destination.OUT, RNS.Destination.SINGLE,
            RETICULUM_APP_NAME, RETICULUM_ASPECT
        )
        print(f"Reticulum: forwarding results to monitor ({dest_hash_hex[:12]}...)")
        return dest
    except Exception as e:
        print(f"WARNING: Reticulum forwarding setup failed ({e}) - continuing without it.")
        return None


def forward_result(dest, seq, status, rtt=None, rssi_l=None, snr_l=None, rssi_r=None, snr_r=None):
    if dest is None:
        return
    payload = {"seq": seq, "status": status}
    if status == "ok":
        payload.update(rtt_ms=rtt, rssi_local=rssi_l, snr_local=snr_l, rssi_remote=rssi_r, snr_remote=snr_r)
    try:
        import RNS  # already imported successfully once in setup_reticulum_forwarder; cached by Python
        RNS.Packet(dest, json.dumps(payload).encode("utf-8")).send()
    except Exception:
        pass  # best-effort - never let a forwarding hiccup interrupt the local test


class Stats:
    def __init__(self):
        self.sent = 0
        self.received = 0
        self.rssi_local = []
        self.rssi_remote = []
        self.snr_local = []
        self.snr_remote = []
        self.rtt_ms = []

    def add_ok(self, rtt, rssi_l, snr_l, rssi_r, snr_r):
        self.received += 1
        self.rtt_ms.append(rtt)
        self.rssi_local.append(rssi_l)
        self.rssi_remote.append(rssi_r)
        self.snr_local.append(snr_l)
        self.snr_remote.append(snr_r)

    def loss_pct(self):
        return 100.0 * (self.sent - self.received) / self.sent if self.sent else 0.0

    def status_line(self, seq):
        if not self.rssi_local:
            return f"seq={seq}  sent={self.sent} recv={self.received} loss={self.loss_pct():.1f}%  (no replies yet)"
        return (
            f"seq={seq}  sent={self.sent} recv={self.received} loss={self.loss_pct():.1f}%  "
            f"RSSI(local/remote)={self.rssi_local[-1]:.0f}/{self.rssi_remote[-1]:.0f} dBm  "
            f"SNR(local/remote)={self.snr_local[-1]:.1f}/{self.snr_remote[-1]:.1f} dB  "
            f"avg_rtt={sum(self.rtt_ms) / len(self.rtt_ms):.0f}ms"
        )

    def final_summary(self):
        lines = [f"Sent: {self.sent}  Received: {self.received}  Loss: {self.loss_pct():.1f}%"]
        if self.rssi_local:
            lines.append(
                f"RSSI local  (gateway's view of field): min={min(self.rssi_local):.0f} "
                f"avg={sum(self.rssi_local)/len(self.rssi_local):.0f} max={max(self.rssi_local):.0f} dBm"
            )
            lines.append(
                f"RSSI remote (field's view of gateway): min={min(self.rssi_remote):.0f} "
                f"avg={sum(self.rssi_remote)/len(self.rssi_remote):.0f} max={max(self.rssi_remote):.0f} dBm"
            )
            lines.append(
                f"SNR local:  min={min(self.snr_local):.1f} avg={sum(self.snr_local)/len(self.snr_local):.1f} "
                f"max={max(self.snr_local):.1f} dB"
            )
            lines.append(
                f"SNR remote: min={min(self.snr_remote):.1f} avg={sum(self.snr_remote)/len(self.snr_remote):.1f} "
                f"max={max(self.snr_remote):.1f} dB"
            )
            lines.append(f"RTT: min={min(self.rtt_ms)}ms avg={sum(self.rtt_ms)/len(self.rtt_ms):.0f}ms max={max(self.rtt_ms)}ms")
        return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", required=True, help="Gateway serial port, e.g. /dev/ttyACM0 or /dev/cu.usbmodemXXXX")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--interval-ms", type=int, default=1000, help="Ping interval in ms (default 1000)")
    parser.add_argument("--duration", type=float, default=None, help="Auto-stop after this many seconds (default: run until Ctrl-C)")
    parser.add_argument("--log", default=None, help="CSV output path (default: lora-tracer-code/tools/range_test_logs/range_<timestamp>.csv)")
    parser.add_argument("--reticulum-dest", default=None, help="range_test_monitor.py's destination hash, to forward live results over Reticulum")
    args = parser.parse_args()

    log_path = args.log or default_log_path()
    with open(log_path, "w", newline="") as f:
        csv.writer(f).writerow(CSV_FIELDS)

    reticulum_dest = setup_reticulum_forwarder(args.reticulum_dest) if args.reticulum_dest else None

    print(f"Opening {args.port} @ {args.baud}...")
    ser = serial.Serial(args.port, args.baud, timeout=1)
    time.sleep(2)  # let the board finish any boot chatter

    stats = Stats()
    stop_sent = False
    start_time = time.time()

    def send_stop():
        nonlocal stop_sent
        if not stop_sent:
            ser.write(b"RANGE:STOP\n")
            stop_sent = True

    print(f"Starting range test: interval={args.interval_ms}ms, logging to {log_path}")
    print("Walk the field node now. Ctrl-C to stop early.\n")
    ser.write(f"RANGE:START:{args.interval_ms}\n".encode("utf-8"))

    try:
        while True:
            if args.duration is not None and (time.time() - start_time) > args.duration:
                send_stop()

            line = ser.readline().decode("utf-8", errors="replace").strip()
            if not line:
                continue

            ts = time.strftime("%Y-%m-%d %H:%M:%S")

            m = PING_RE.search(line)
            if m:
                # Track the send silently - don't refresh the status line
                # here, since the round trip (OK/MISS) hasn't resolved yet.
                # Printing on every send as well as every resolution made
                # loss% flicker between a transient "still waiting" value
                # and the real one, which reads as fake intermittent loss.
                stats.sent += 1
                continue

            m = OK_RE.search(line)
            if m:
                seq, rtt, rssi_l, snr_l, rssi_r, snr_r = m.groups()
                rtt, rssi_l, snr_l, rssi_r, snr_r = int(rtt), float(rssi_l), float(snr_l), float(rssi_r), float(snr_r)
                stats.add_ok(rtt, rssi_l, snr_l, rssi_r, snr_r)
                with open(log_path, "a", newline="") as f:
                    csv.writer(f).writerow([ts, seq, "ok", rtt, rssi_l, snr_l, rssi_r, snr_r])
                forward_result(reticulum_dest, int(seq), "ok", rtt, rssi_l, snr_l, rssi_r, snr_r)
                sys.stdout.write("\r" + stats.status_line(int(seq)) + "   ")
                sys.stdout.flush()
                continue

            m = MISS_RE.search(line)
            if m:
                seq = int(m.group(1))
                with open(log_path, "a", newline="") as f:
                    csv.writer(f).writerow([ts, seq, "miss", "", "", "", "", ""])
                forward_result(reticulum_dest, seq, "miss")
                sys.stdout.write("\r" + stats.status_line(seq) + "   ")
                sys.stdout.flush()
                continue

            m = SUMMARY_RE.search(line)
            if m:
                print("\n\nTest stopped. Gateway-reported summary:")
                print(f"  sent={m.group(1)} received={m.group(2)} loss_pct={m.group(3)}")
                break

    except KeyboardInterrupt:
        print("\n\nStopping...")
        send_stop()
        # Give the gateway a moment to flush its own SUMMARY line before we
        # print ours and exit.
        deadline = time.time() + 3
        while time.time() < deadline:
            line = ser.readline().decode("utf-8", errors="replace").strip()
            if SUMMARY_RE.search(line):
                break

    print("\nLocal summary (from packets actually seen by this script):")
    print(stats.final_summary())
    print(f"\nFull log: {log_path}")
    ser.close()


if __name__ == "__main__":
    main()
