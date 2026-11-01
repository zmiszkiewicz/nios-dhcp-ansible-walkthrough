"""lease_gen.py against a tiny DHCP server on loopback.

The server plays NIOS's part: it chooses an address from the relay address
(giaddr) in the request, and replies to the sender. The test proves the client
sends what a relay agent would (giaddr set, the exact MAC, DISCOVER then REQUEST
with the offered address and server identifier) and that it handles missing
replies. It does not prove NIOS accepts it; that needs a live grid.
"""
import socket
import struct
import threading

import pytest

import lab_config as cfg
import lease_gen as gen

SERVER_PORT, CLIENT_PORT = 15067, 15068


def server_reply(request, msg_type, yiaddr):
    """A BOOTREPLY for `request`, as a DHCP server builds it."""
    chaddr = request["mac"].replace(":", "")
    header = struct.pack(
        "!BBBBIHH4s4s4s4s16s64s128s", 2, 1, 6, 0, request["xid"], 0, 0,
        b"\0" * 4, socket.inet_aton(yiaddr), b"\0" * 4, socket.inet_aton(request["giaddr"]),
        bytes.fromhex(chaddr).ljust(16, b"\0"), b"", b"")
    options = bytes([53, 1, msg_type, 54, 4]) + socket.inet_aton("10.100.0.21") \
        + bytes([51, 4]) + struct.pack("!I", 86400) + bytes([255])
    return header + gen.MAGIC + options


class MiniDhcpServer:
    """Hands out .100, .101, ... in the subnet of the request's giaddr; drops chosen MACs."""

    def __init__(self, drop_macs=()):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", SERVER_PORT))
        self.sock.settimeout(0.2)
        self.drop_macs = set(drop_macs)
        self.offered = {}      # mac -> address
        self.seen = []         # parsed requests, in order
        self.running = True
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self):
        while self.running:
            try:
                data, source = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            request = gen.parse_packet(data)
            self.seen.append(request)
            if request["mac"] in self.drop_macs:
                continue
            subnet = ".".join(request["giaddr"].split(".")[:3])
            if request["type"] == gen.DISCOVER:
                address = self.offered.setdefault(request["mac"], f"{subnet}.{100 + len(self.offered)}")
                self.sock.sendto(server_reply(request, gen.OFFER, address), source)
            elif request["type"] == gen.REQUEST:
                wanted = socket.inet_ntoa(request["options"][gen.OPT_REQUESTED_IP])
                kind = gen.ACK if self.offered.get(request["mac"]) == wanted else gen.NAK
                self.sock.sendto(server_reply(request, kind, wanted), source)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.running = False
        self.thread.join(timeout=2)
        self.sock.close()


def relay(timeout=3.0):
    return gen.Relay("127.0.0.1", "127.0.0.1", SERVER_PORT, CLIENT_PORT, timeout)


def test_packet_round_trip():
    packet = gen.build_packet(gen.REQUEST, "02:00:00:01:f0:02", 0xDEADBEEF, "10.100.11.20",
                              requested_ip="10.100.11.60", server_id="10.100.0.21", hostname="printer-br1001")
    parsed = gen.parse_packet(packet)
    assert parsed["type"] == gen.REQUEST and parsed["xid"] == 0xDEADBEEF
    assert parsed["giaddr"] == "10.100.11.20"
    assert parsed["mac"] == "02:00:00:01:f0:02"
    assert parsed["options"][gen.OPT_HOSTNAME] == b"printer-br1001"
    assert socket.inet_ntoa(parsed["options"][gen.OPT_REQUESTED_IP]) == "10.100.11.60"
    assert socket.inet_ntoa(parsed["options"][gen.OPT_SERVER_ID]) == "10.100.0.21"
    assert len(packet) >= 300  # BOOTP minimum


def test_parse_rejects_non_dhcp():
    with pytest.raises(ValueError):
        gen.parse_packet(b"\x00" * 300)


def test_mac_rule_matches_lab_config():
    for scope in (1, 2, 3):
        for client in (1, 10, 30):
            assert gen.mac_for(scope, client) == cfg.mac_for(scope, client)
    assert gen.mac_for(1, 30) == "02:00:00:01:00:1e"


def test_clients_get_leases_through_the_relay():
    clients = [{"mac": gen.mac_for(1, n), "hostname": f"br1001-pc-{n:03d}"} for n in range(1, 6)]
    with MiniDhcpServer() as server:
        results = relay().run(clients)
    assert [r["status"] for r in results] == ["ACK"] * 5
    assert {r["mac"] for r in results} == {c["mac"] for c in clients}
    assert len({r["ip"] for r in results}) == 5                # distinct addresses
    assert all(r["lease_seconds"] == 86400 for r in results)
    # every request carried the relay address, so the server picked the right subnet
    assert {p["giaddr"] for p in server.seen} == {"127.0.0.1"}
    assert all(r["ip"].startswith("127.0.0.") for r in results)


def test_discover_and_request_sequence_and_server_id():
    with MiniDhcpServer() as server:
        relay().run([{"mac": "02:00:00:01:f0:02", "hostname": "printer-br1001"}])
    kinds = [p["type"] for p in server.seen]
    assert kinds == [gen.DISCOVER, gen.REQUEST]
    request = server.seen[1]
    assert socket.inet_ntoa(request["options"][gen.OPT_SERVER_ID]) == "10.100.0.21"
    assert request["mac"] == "02:00:00:01:f0:02"               # the exact MAC, not a randomized one


def test_a_client_the_server_ignores_is_reported_not_hidden():
    clients = [{"mac": gen.mac_for(1, 1)}, {"mac": gen.mac_for(1, 2)}]
    with MiniDhcpServer(drop_macs={gen.mac_for(1, 2)}):
        results = {r["mac"]: r["status"] for r in relay(timeout=1.5).run(clients)}
    assert results == {gen.mac_for(1, 1): "ACK", gen.mac_for(1, 2): "NO_OFFER"}


def test_cli_exit_status_and_output(capsys):
    argv = ["--server", "127.0.0.1", "--giaddr", "127.0.0.1", "--server-port", str(SERVER_PORT),
            "--local-port", str(CLIENT_PORT), "--count", "3", "--scope-index", "2", "--timeout", "3"]
    with MiniDhcpServer():
        assert gen.main(argv) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert '"summary": true' in lines[-1] and '"leased": 3' in lines[-1]
    assert '"mac": "02:00:00:02:00:01"' in lines[0]


def test_cli_exit_status_2_when_some_fail(capsys):
    argv = ["--server", "127.0.0.1", "--giaddr", "127.0.0.1", "--server-port", str(SERVER_PORT),
            "--local-port", str(CLIENT_PORT), "--count", "2", "--timeout", "1.5"]
    with MiniDhcpServer(drop_macs={gen.mac_for(1, 2)}):
        assert gen.main(argv) == 2
