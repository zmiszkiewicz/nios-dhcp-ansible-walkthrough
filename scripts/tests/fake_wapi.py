"""A small in-memory stand-in for the parts of NIOS WAPI this lab uses.

It is not NIOS. It exists so the scripts and the real Ansible modules can be run
offline: object create/search/update/delete, extensible-attribute search, the
create_token and downloadcertificate functions, and a member that "joins" on
demand. It serves HTTPS with a throwaway certificate because the Ansible
collection's client (infoblox-client) only speaks HTTPS. What it proves is that
the lab's code sends well-formed requests and handles the answers it expects;
real NIOS behaviour still has to be confirmed on a live grid.

Run standalone to poke at it:  python3 fake_wapi.py 8443
"""
from __future__ import annotations

import base64
import json
import ssl
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

KEYS = {
    "network": ("network",), "range": ("start_addr", "end_addr"),
    "fixedaddress": ("ipv4addr",), "extensibleattributedef": ("name",),
    "member": ("host_name",),
}

PEM = "-----BEGIN CERTIFICATE-----\nZmFrZS1ncmlkLW1hc3Rlci1jZXJ0aWZpY2F0ZQ==\n-----END CERTIFICATE-----\n"


class FakeGrid:
    def __init__(self):
        self.objects = {}   # ref -> (type, data)
        self.counter = 0
        self.tokens = 0
        self.reject_macs = set()   # tests: refuse to create a fixedaddress for these MACs
        self.add("grid", {"name": "Infoblox"})
        self.add("member", {"host_name": "infoblox.localdomain",
                            "vip_setting": {"address": "10.100.0.11"}, "node_info": []})
        self.add("member:dhcpproperties", {"host_name": "infoblox.localdomain", "enable_dhcp": True})

    def add(self, obj_type, data):
        self.counter += 1
        label = ":".join(str(data.get(k, "")) for k in KEYS.get(obj_type, ("name",)))
        ref = f"{obj_type}/ZG{self.counter}:{label}"
        self.objects[ref] = (obj_type, dict(data, _ref=ref))
        return ref

    def of_type(self, obj_type):
        return [d for t, d in self.objects.values() if t == obj_type]

    def find(self, obj_type, **fields):
        for data in self.of_type(obj_type):
            if all(data.get(k) == v for k, v in fields.items()):
                return data
        return None

    def matches(self, data, search):
        for key, want in search.items():
            if key.startswith("*"):
                if str(data.get("extattrs", {}).get(key[1:], {}).get("value")) != want:
                    return False
            elif str(data.get(key, "")).lower() != want.lower():
                return False
        return True

    # --- helpers tests use to set the scene ---------------------------------
    def join_member(self, host_name):
        """Make a pre-provisioned member report as joined and working."""
        data = self.find("member", host_name=host_name)
        data["node_info"] = [{"service_status": [{"service": "NODE_STATUS", "status": "WORKING"},
                                                 {"service": "DHCP", "status": "WORKING"}]}]

    def add_lease(self, network, address, mac, state="ACTIVE"):
        self.add("lease", {"network": network, "address": address, "hardware": mac,
                           "binding_state": state, "network_view": "default"})


def make_handler(grid, password):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body, content_type="application/json"):
            data = body.encode() if isinstance(body, str) and content_type != "application/json" \
                else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _route(self, method):
            expected = "Basic " + base64.b64encode(f"admin:{password}".encode()).decode()
            if self.headers.get("Authorization") != expected:
                return self._send(401, {"text": "unauthorized"})
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length)) if length else {}

            if url.path.startswith("/http_direct_file_io/"):
                # Like NIOS: a download without this content type is refused with 415.
                if self.headers.get("Content-Type") != "application/force-download":
                    return self._send(415, "Unsupported Media Type", content_type="text/plain")
                return self._send(200, PEM, content_type="text/plain")
            if not url.path.startswith("/wapi/"):
                return self._send(404, {"text": "not found"})
            target = unquote(url.path.split("/wapi/", 1)[1].split("/", 1)[1])

            function = query.get("_function")
            search = {k: v for k, v in query.items() if not k.startswith("_")}

            if function:
                return self._function(target, function, payload)
            if "/" in target:  # a _ref
                return self._by_ref(method, target, payload)
            if method == "GET":
                if target == "lease" and "binding_state" in search:
                    # WAPI 2.12.3: the field can be returned but not searched.
                    return self._send(400, {"Error": "AdmConProtoError: Field is not searchable: binding_state",
                                            "code": "Client.Ibap.Proto",
                                            "text": "Field is not searchable: binding_state"})
                found = [d for d in grid.of_type(target) if grid.matches(d, search)]
                limit = int(query.get("_max_results", 0) or 0)
                return self._send(200, found[:limit] if limit > 0 else found)
            if method == "POST":
                return self._create(target, payload)
            return self._send(400, {"text": f"unsupported {method} on {target}"})

        def _create(self, obj_type, payload):
            if obj_type == "range" and (payload.get("member") or {}).get("name"):
                # Like NIOS: a member can only serve a range in a network assigned to that member.
                network = grid.find("network", network=payload.get("network")) or {}
                assigned = [m.get("name") for m in network.get("members", [])]
                if payload["member"]["name"] not in assigned:
                    return self._send(400, {"code": "Client.Ibap.Data.Conflict", "text":
                        f"Member {payload['member']['name']} is assigned to the DHCP range, "
                        "but is not assigned to the selected network"})
            if obj_type == "fixedaddress" and payload.get("mac") in grid.reject_macs:
                return self._send(400, {"text": "Rejected by the test grid"})
            keys = KEYS.get(obj_type)
            if keys and grid.find(obj_type, **{k: payload.get(k) for k in keys}):
                return self._send(400, {"text": f"{obj_type} already exists"})
            ref = grid.add(obj_type, payload)
            if obj_type == "member":
                grid.add("member:dhcpproperties", {"host_name": payload["host_name"], "enable_dhcp": False})
            return self._send(201, ref)

        def _by_ref(self, method, ref, payload):
            if ref not in grid.objects:
                return self._send(404, {"text": f"{ref} not found"})
            obj_type, data = grid.objects[ref]
            if method == "GET":
                return self._send(200, data)
            if method == "PUT":
                data.update(payload)
                return self._send(200, ref)
            if method == "DELETE":
                del grid.objects[ref]
                return self._send(200, ref)
            return self._send(400, {"text": f"unsupported {method}"})

        def _function(self, target, function, payload):
            if function == "create_token":
                grid.tokens += 1
                return self._send(200, {"pnode_tokens": [{"token": f"token-{grid.tokens}"}]})
            if function == "downloadcertificate":
                host = self.headers.get("Host")
                return self._send(200, {"url": f"https://10.100.0.11/http_direct_file_io/cert/cert.pem",
                                        "token": "t", "_host": host})
            return self._send(200, {})

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def do_PUT(self):
            self._route("PUT")

        def do_DELETE(self):
            self._route("DELETE")

    return Handler


def make_certificate(directory):
    cert, key = Path(directory) / "cert.pem", Path(directory) / "key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
         "-keyout", str(key), "-out", str(cert), "-subj", "/CN=localhost"],
        check=True, capture_output=True)
    return cert, key


class FakeServer:
    """Context manager: HTTPS on an ephemeral port; `.host` is '127.0.0.1:<port>'."""

    def __init__(self, password="test", port=0):
        self.grid = FakeGrid()
        self.password = password
        self._tmp = tempfile.TemporaryDirectory()
        cert, key = make_certificate(self._tmp.name)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(self.grid, password))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert), str(key))
        self.httpd.socket = context.wrap_socket(self.httpd.socket, server_side=True)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def host(self):
        return f"127.0.0.1:{self.httpd.server_address[1]}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self._tmp.cleanup()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8443
    with FakeServer(port=port) as server:
        print(f"fake WAPI on https://{server.host}/wapi/v2.12.3/  (admin / test)")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
