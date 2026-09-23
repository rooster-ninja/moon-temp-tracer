import serial
import json
import time
import RNS

SERIAL_PORT = "/dev/ttyACM0"
BAUD = 115200

# Destination hash of the receiving Reticulum identity (Mac's hello.py, for now)
PEER_HASH_HEX = "d8560d80c4d1dbad237c08b95b711cc5"
APP_NAME = "helloworld"
ASPECT = "node"

def main():
    reticulum = RNS.Reticulum()

    identity_path = RNS.Reticulum.storagepath + "/bridge_identity"
    try:
        my_identity = RNS.Identity.from_file(identity_path)
        if my_identity is None:
            my_identity = RNS.Identity()
            my_identity.to_file(identity_path)
    except Exception:
        my_identity = RNS.Identity()
        my_identity.to_file(identity_path)

    peer_hash = bytes.fromhex(PEER_HASH_HEX)
    if not RNS.Transport.has_path(peer_hash):
        print("No path to peer yet, requesting...")
        RNS.Transport.request_path(peer_hash)
        time.sleep(3)

    peer_identity = RNS.Identity.recall(peer_hash)
    if not peer_identity:
        print("Could not recall peer identity. Is the peer announced?")
        return

    peer_dest = RNS.Destination(
        peer_identity, RNS.Destination.OUT, RNS.Destination.SINGLE,
        APP_NAME, ASPECT
    )

    ser = serial.Serial(SERIAL_PORT, BAUD, timeout=1)
    print(f"Bridge listening on {SERIAL_PORT}, forwarding to peer...")

    while True:
        line = ser.readline().decode("utf-8", errors="replace").strip()
        if not line:
            continue
        if line.startswith("RNS:"):
            payload = line[4:]
            try:
                data = json.loads(payload)
                text = json.dumps(data)
                RNS.Packet(peer_dest, text.encode("utf-8")).send()
                print(f"Forwarded to Reticulum: {text}")
            except json.JSONDecodeError:
                print(f"Malformed RNS line, skipping: {payload}")
        else:
            print(line)

if __name__ == "__main__":
    main()
