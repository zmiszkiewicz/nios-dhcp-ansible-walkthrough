#!/usr/bin/env python3
"""Get real DHCP leases from a NIOS member, as clients behind a branch router.

NIOS chooses a DHCP scope from the relay address (giaddr) in a request and sends
the reply to that address. This tool acts as the relay for one branch: it binds
the branch's own address (the simulator owns one in every branch subnet), sends
DISCOVER and REQUEST unicast to the member from that address with giaddr set to
it, and receives the OFFER and ACK there. To NIOS this is indistinguishable from
a client behind a real branch router, so the leases are real leases.

It exists, instead of perfdhcp, because the lab needs exact control of the client
MAC addresses (the branch printer must request with one specific MAC, and the
MAC lists pushed to the wake-up servers must be reproducible) and because it is
a single file with no packages to install.

Run it as root on the simulator (it binds UDP port 67):

  # 30 clients for the first scope (MACs 02:00:00:01:00:01 ... 02:00:00:01:00:1e)
  sudo python3 lease_gen.py --server 10.100.0.21 --giaddr 10.100.11.20 \\
       --count 30 --scope-index 1 --hostname-prefix br1001-pc

  # One device with a chosen MAC (the branch printer)
  sudo python3 lease_gen.py --server 10.100.0.21 --giaddr 10.100.11.20 \\
       --mac 02:00:00:01:f0:02 --hostname printer-br1001

Prints one JSON line per client, then a summary line. Exit status 0 when every
client got a lease, 2 when some did not.
"""
from __future__ import annotations

import argparse
import json
import random
import socket
import struct
import sys
import time

MAGIC = b"\x63\x82\x53\x63"
DISCOVER, OFFER, REQUEST, ACK, NAK = 1, 2, 3, 5, 6
OPT_HOSTNAME, OPT_REQUESTED_IP, OPT_LEASE_TIME = 12, 50, 51
OPT_MSG_TYPE, OPT_SERVER_ID, OPT_PARAM_LIST, OPT_CLIENT_ID, OPT_END = 53, 54, 55, 61, 255


def mac_for(scope_index, client_index):
    """Same rule as lab_config.mac_for (checked by a test); this file stands alone."""
    return f"02:00:00:{scope_index:02x}:00:{client_index:02x}"


def mac_bytes(mac):
    return bytes.fromhex(mac.replace(":", "").replace("-", ""))


def build_packet(msg_type, mac, xid, giaddr, requested_ip=None, server_id=None, hostname=None):
    """A DHCPv4 client message as a relay agent forwards it (giaddr set, unicast reply path)."""
    chaddr = mac_bytes(mac).ljust(16, b"\x00")
    header = struct.pack(
        "!BBBBIHH4s4s4s4s16s64s128s",
        1, 1, 6, 0,            # op=BOOTREQUEST, htype=ethernet, hlen=6, hops=0
        xid, 0, 0,             # xid, secs, flags (0: reply is unicast to the relay)
        b"\0" * 4, b"\0" * 4, b"\0" * 4,   # ciaddr, yiaddr, siaddr
        socket.inet_aton(giaddr),
        chaddr, b"", b"",      # chaddr, sname, file
    )
    options = bytearray()

    def add(code, data):
        options.extend([code, len(data)])
        options.extend(data)

    add(OPT_MSG_TYPE, bytes([msg_type]))
    add(OPT_CLIENT_ID, b"\x01" + mac_bytes(mac))
    if hostname:
        add(OPT_HOSTNAME, hostname.encode("ascii"))
    if requested_ip:
        add(OPT_REQUESTED_IP, socket.inet_aton(requested_ip))
    if server_id:
        add(OPT_SERVER_ID, socket.inet_aton(server_id))
    add(OPT_PARAM_LIST, bytes([1, 3, 6, 15, 51, 54]))
    options.append(OPT_END)
    # Real clients pad to the 300-byte BOOTP minimum, and some servers are strict about it.
    return (header + MAGIC + bytes(options)).ljust(300, b"\x00")


def parse_packet(data):
    """Decode a DHCPv4 packet into a dict. Raises ValueError if it is not one."""
    if len(data) < 240 or data[236:240] != MAGIC:
        raise ValueError("not a DHCP packet")
    op, _htype, hlen, _hops, xid, _secs, _flags = struct.unpack("!BBBBIHH", data[:12])
    options = {}
    i = 240
    while i < len(data):
        code = data[i]
        if code == OPT_END:
            break
        if code == 0:
            i += 1
            continue
        length = data[i + 1]
        options[code] = data[i + 2:i + 2 + length]
        i += 2 + length
    return {
        "op": op,
        "xid": xid,
        "yiaddr": socket.inet_ntoa(data[16:20]),
        "giaddr": socket.inet_ntoa(data[24:28]),
        "mac": ":".join(f"{b:02x}" for b in data[28:28 + hlen]),
        "options": options,
        "type": options.get(OPT_MSG_TYPE, b"\x00")[0],
    }


class Relay:
    """One branch's relay address, used to run DORA exchanges for many clients at once."""

    def __init__(self, server, giaddr, server_port=67, local_port=67, timeout=15.0):
        self.server = (server, server_port)
        self.giaddr = giaddr
        self.timeout = timeout
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((giaddr, local_port))
        self.sock.settimeout(0.25)

    def close(self):
        self.sock.close()

    def _collect(self, pending, wanted_types, seconds):
        """Receive until every xid in `pending` has a reply of a wanted type, or time runs out."""
        replies = {}
        deadline = time.time() + seconds
        while len(replies) < len(pending) and time.time() < deadline:
            try:
                data, _ = self.sock.recvfrom(4096)
                packet = parse_packet(data)
            except (socket.timeout, ValueError):
                continue
            if packet["xid"] in pending and packet["type"] in wanted_types:
                replies[packet["xid"]] = packet
        return replies

    def run(self, clients):
        """clients: [{'mac':..., 'hostname':...}]. Returns a result dict per client."""
        by_xid = {}
        for client in clients:
            xid = random.getrandbits(32)
            while xid in by_xid:
                xid = random.getrandbits(32)
            by_xid[xid] = client

        offers = {}
        for _ in range(3):  # a missed packet is retried, as a real client would
            todo = {x for x in by_xid if x not in offers}
            if not todo:
                break
            for xid in todo:
                c = by_xid[xid]
                self.sock.sendto(build_packet(DISCOVER, c["mac"], xid, self.giaddr,
                                              hostname=c.get("hostname")), self.server)
            offers.update(self._collect(todo, {OFFER}, self.timeout / 3))

        acks = {}
        for _ in range(3):
            todo = {x for x in offers if x not in acks}
            if not todo:
                break
            for xid in todo:
                c, offer = by_xid[xid], offers[xid]
                server_id = offer["options"].get(OPT_SERVER_ID)
                self.sock.sendto(build_packet(
                    REQUEST, c["mac"], xid, self.giaddr,
                    requested_ip=offer["yiaddr"],
                    server_id=socket.inet_ntoa(server_id) if server_id else None,
                    hostname=c.get("hostname")), self.server)
            acks.update(self._collect(todo, {ACK, NAK}, self.timeout / 3))

        results = []
        for xid, client in by_xid.items():
            ack = acks.get(xid)
            if ack and ack["type"] == ACK:
                lease = ack["options"].get(OPT_LEASE_TIME)
                results.append({"mac": client["mac"], "hostname": client.get("hostname"),
                                "ip": ack["yiaddr"], "status": "ACK",
                                "lease_seconds": struct.unpack("!I", lease)[0] if lease else None})
            elif ack:
                results.append({"mac": client["mac"], "hostname": client.get("hostname"),
                                "ip": None, "status": "NAK", "lease_seconds": None})
            elif xid in offers:
                results.append({"mac": client["mac"], "hostname": client.get("hostname"),
                                "ip": None, "status": "NO_ACK", "lease_seconds": None})
            else:
                results.append({"mac": client["mac"], "hostname": client.get("hostname"),
                                "ip": None, "status": "NO_OFFER", "lease_seconds": None})
        return results


def client_list(args):
    if args.mac:
        return [{"mac": args.mac.lower().replace("-", ":"), "hostname": args.hostname}]
    return [{"mac": mac_for(args.scope_index, n),
             "hostname": f"{args.hostname_prefix}-{n:03d}"}
            for n in range(1, args.count + 1)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--server", required=True, help="the member's DHCP address (LAN1)")
    parser.add_argument("--giaddr", required=True, help="this branch's relay address, owned by this host")
    parser.add_argument("--count", type=int, default=30, help="how many clients (default 30)")
    parser.add_argument("--scope-index", type=int, default=1, help="1-based branch number, used in generated MACs")
    parser.add_argument("--hostname-prefix", default="client")
    parser.add_argument("--mac", help="request as this single MAC instead of generating a population")
    parser.add_argument("--hostname", help="client hostname (with --mac)")
    parser.add_argument("--server-port", type=int, default=67)
    parser.add_argument("--local-port", type=int, default=67)
    parser.add_argument("--timeout", type=float, default=15.0, help="seconds to wait per phase")
    args = parser.parse_args(argv)

    try:
        relay = Relay(args.server, args.giaddr, args.server_port, args.local_port, args.timeout)
    except OSError as exc:
        print(f"cannot bind {args.giaddr}:{args.local_port}: {exc} (run as root on the simulator)",
              file=sys.stderr)
        return 1
    try:
        results = relay.run(client_list(args))
    finally:
        relay.close()

    for result in results:
        print(json.dumps(result))
    ok = sum(1 for r in results if r["status"] == "ACK")
    print(json.dumps({"summary": True, "giaddr": args.giaddr, "server": args.server,
                      "requested": len(results), "leased": ok}))
    return 0 if ok == len(results) else 2


if __name__ == "__main__":
    sys.exit(main())
