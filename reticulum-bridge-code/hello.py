import sys
import time
import RNS

APP_NAME = "helloworld"
ASPECT = "node"

def received(message, packet):
    text = message.decode("utf-8", errors="replace")
    print(f"\n[RECEIVED] {text}")

def main():
    reticulum = RNS.Reticulum()
    identity_path = RNS.Reticulum.storagepath + "/hello_identity"
    ratchet_path = RNS.Reticulum.storagepath + "/hello_ratchets"

    try:
        my_identity = RNS.Identity.from_file(identity_path)
        if my_identity == None:
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
    my_dest.set_packet_callback(received)
    my_dest.enable_ratchets(ratchet_path)
    my_dest.announce()

    print(f"My destination hash: {RNS.prettyhexrep(my_dest.hash)}")
    print("Announced. Waiting a moment for peer discovery...\n")
    time.sleep(2)

    peer_hash_hex = input("Enter peer's destination hash (or leave blank to just listen): ").strip()

    peer_dest = None
    if peer_hash_hex:
        peer_hash = bytes.fromhex(peer_hash_hex)
        if not RNS.Transport.has_path(peer_hash):
            print("No path to peer yet, requesting...")
            RNS.Transport.request_path(peer_hash)
            time.sleep(3)
        peer_identity = RNS.Identity.recall(peer_hash)
        if peer_identity:
            peer_dest = RNS.Destination(
                peer_identity, RNS.Destination.OUT, RNS.Destination.SINGLE,
                APP_NAME, ASPECT
            )
        else:
            print("Could not recall peer identity yet — try again shortly.")

    print("Type a message and hit enter to send. Ctrl-C to quit.\n")
    while True:
        try:
            text = input("> ")
            if peer_dest:
                RNS.Packet(peer_dest, text.encode("utf-8")).send()
            else:
                print("No peer set — message not sent.")
        except KeyboardInterrupt:
            sys.exit(0)

if __name__ == "__main__":
    main()
