#!/usr/bin/env python3
"""Check that a challenge's objects really exist. Used by the track's check-shell.

  python3 verify_lab.py --stage baseline|scopes|leases|macs|reservation

Looks at state, not at what was typed: networks, ranges and leases are read back
from the grid, and the wake-up files are hashed on the Windows servers and
compared with what the grid says they should contain. The expected content is
computed here independently of the Ansible playbooks, so a check passing means
two implementations agree. On failure a one-sentence reason is written to
/tmp/lab_check_reason.txt, which check-shell hands to Instruqt as the message.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import subprocess
import sys
from pathlib import Path

import yaml

import lab_config as cfg
from join_members import service_statuses
from nios_wapi import NiosWapi, WapiError, flatten_ea, load_state
from winrm_helper import WinRMError, run_ps

REASON_FILE = Path("/tmp/lab_check_reason.txt")
REPO_ROOT = Path(__file__).resolve().parent.parent
VARS_DIR = REPO_ROOT / "ansible" / "inventory" / "group_vars" / "all"
EXPECTED_EAS = ("Branch", "Site", "WakeUp_Server")


class Check(Exception):
    """Raised with the sentence to show the presenter."""


def load_vars(name):
    return yaml.safe_load((VARS_DIR / name).read_text())


def member_name(key):
    return next(m["name"] for m in cfg.MEMBERS if m["key"] == key)


# --- baseline ---------------------------------------------------------------

def stage_baseline(api, check_hosts=True):
    dhcp_props = {p.get("host_name"): p for p in api.get("member:dhcpproperties", fields=["enable_dhcp"])}
    for member in cfg.MEMBERS:
        obj = api.first("member", params={"host_name": member["name"]}, fields=["node_info"])
        if not obj:
            raise Check(f"{member['name']} is not on the grid. Run: python3 join_members.py prepare")
        if not any(status == "WORKING" for _, status in service_statuses(obj)):
            raise Check(f"{member['name']} has not joined the grid yet. Members take several minutes to "
                        "boot and join; check again shortly.")
        if not dhcp_props.get(member["name"], {}).get("enable_dhcp"):
            raise Check(f"The DHCP service is not enabled on {member['name']}. Run: python3 join_members.py wait")
    if not check_hosts:
        return
    state = load_state("lab")
    if not state:
        raise Check("The lab state is missing. Run: python3 make_inventory.py")
    for name, server in state["wakeup_servers"].items():
        try:
            code, out, _ = run_ps(server["public_ip"], "$env:COMPUTERNAME", timeout=30)
        except WinRMError as exc:
            raise Check(f"Cannot reach wake-up server {name} over WinRM ({exc}). Windows takes a few "
                        "minutes to boot; check again shortly.") from exc
        if code != 0:
            raise Check(f"WinRM on {name} answered but the command failed.")
    probe = subprocess.run(
        ["ssh", "-i", state["ssh_key_path"], "-o", "StrictHostKeyChecking=no",
         "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
         "-o", "LogLevel=ERROR", f"ubuntu@{state['sim_public_ip']}", f"test -f {cfg.SIM_DIR}/lease_gen.py"],
        capture_output=True, text=True, check=False)
    if probe.returncode != 0:
        raise Check("The branch simulator is not ready (SSH failed or lease_gen.py is missing).")


# --- scopes -----------------------------------------------------------------

def stage_scopes(api):
    defined = {e.get("name") for e in api.get("extensibleattributedef")}
    for name in EXPECTED_EAS:
        if name not in defined:
            raise Check(f"The '{name}' extensible attribute is not defined. Run: ansible-playbook playbooks/create_scopes.yml")
    for scope in load_vars("scopes.yml")["scopes"]:
        net = api.first("network", params={"network": scope["network"]}, fields=["extattrs", "members"])
        if not net:
            raise Check(f"Network {scope['network']} does not exist. Run: ansible-playbook playbooks/create_scopes.yml")
        eas = flatten_ea(net.get("extattrs"))
        for key, want in scope["extattrs"].items():
            if eas.get(key) != want:
                raise Check(f"{scope['network']} has {key}={eas.get(key)!r}, expected {want!r}.")
        assigned = [m.get("name") for m in net.get("members") or []]
        if member_name(scope["member"]) not in assigned:
            raise Check(f"{scope['network']} is not assigned to {member_name(scope['member'])}. "
                        "Run: ansible-playbook playbooks/create_scopes.yml")
        rng = api.first("range", params={"network": scope["network"]}, fields=["member", "server_association_type"])
        if not rng or rng.get("start_addr") != scope["range_start"] or rng.get("end_addr") != scope["range_end"]:
            raise Check(f"{scope['network']} has no DHCP range {scope['range_start']} - {scope['range_end']}.")
        served_by = (rng.get("member") or {}).get("name")
        if served_by != member_name(scope["member"]):
            raise Check(f"The range in {scope['network']} is served by {served_by!r}, "
                        f"expected {member_name(scope['member'])}.")


# --- leases -----------------------------------------------------------------

def active_leases(api, network):
    # binding_state cannot be searched on WAPI 2.12.3; ask for it and filter here.
    leases = [lease for lease in api.get("lease", params={"network": network}, fields=["hardware", "binding_state"])
              if lease.get("binding_state") == "ACTIVE"]
    return sorted(leases, key=lambda lease: ipaddress.ip_address(lease["address"]))


def stage_leases(api, minimum=20):
    for scope in load_vars("scopes.yml")["scopes"]:
        count = len(active_leases(api, scope["network"]))
        if count < minimum:
            raise Check(f"{scope['network']} has {count} active leases, expected at least {minimum}. "
                        "Run: scripts/bring_clients_online.sh")


# --- macs -------------------------------------------------------------------

def expected_wakeup_files(api):
    """{wake-up private IP: file bytes}, computed from the grid the way the playbook does."""
    blank = int(load_vars("main.yml").get("mac_legacy_blank_lines", 2))
    per_server = {}
    for net in api.get("network", fields=["extattrs"]):
        eas = flatten_ea(net.get("extattrs"))
        if "Branch" not in eas:
            continue
        octets = net["network"].split("/")[0].split(".")
        server = eas.get("WakeUp_Server") or ".".join(octets[:3] + ["111"])
        macs = per_server.setdefault(server, [])
        for lease in active_leases(api, net["network"]):
            mac = lease["hardware"].lower()
            if mac not in macs:
                macs.append(mac)
    return {server: ("\r\n" * blank + "".join(f"{m}\r\n" for m in macs)).encode()
            for server, macs in per_server.items()}


def stage_macs(api):
    state = load_state("lab")
    if not state:
        raise Check("The lab state is missing. Run: python3 make_inventory.py")
    by_private = {s["private_ip"]: (name, s["public_ip"]) for name, s in state["wakeup_servers"].items()}
    expected = expected_wakeup_files(api)
    reached = 0
    for private_ip, content in expected.items():
        if private_ip not in by_private:
            continue  # a scope whose server is not in the lab inventory: nothing is pushed for it
        name, public_ip = by_private[private_ip]
        try:
            code, out, _ = run_ps(
                public_ip,
                f"if (Test-Path '{cfg.WAKEUP_FILE}') {{ (Get-FileHash -Algorithm SHA1 '{cfg.WAKEUP_FILE}').Hash.ToLower() }} else {{ 'missing' }}",
                timeout=60)
        except WinRMError as exc:
            raise Check(f"Cannot reach {name} over WinRM ({exc}).") from exc
        got = out.strip()
        if got == "missing":
            raise Check(f"{cfg.WAKEUP_FILE} does not exist on {name}. Run: ansible-playbook playbooks/push_macs.yml")
        if got != hashlib.sha1(content).hexdigest():
            raise Check(f"{cfg.WAKEUP_FILE} on {name} does not match the leases on the grid. "
                        "Run: ansible-playbook playbooks/push_macs.yml")
        reached += 1
    if not reached:
        raise Check("No wake-up server in the lab inventory matches a branch scope.")


# --- reservation --------------------------------------------------------------

def stage_reservation(api, mac=None):
    mac = (mac or cfg.PRINTER_NEW_MAC).lower()
    fixed = api.first("fixedaddress", params={"ipv4addr": cfg.PRINTER_IP})
    if not fixed:
        raise Check(f"{cfg.PRINTER_IP} has no reservation at all. Run: ansible-playbook playbooks/seed_printer.yml")
    if fixed.get("mac", "").lower() != mac:
        raise Check(f"{cfg.PRINTER_IP} is still reserved for {fixed.get('mac')}. Run: ansible-playbook "
                    f"playbooks/replace_reservation.yml -e ip={cfg.PRINTER_IP} -e mac={mac}")
    log = REPO_ROOT / "logs" / "reservation-changes.txt"
    lines = log.read_text().split() if log.exists() else []
    if lines[-2:] != [cfg.PRINTER_IP, mac]:
        raise Check("The change was not logged in logs/reservation-changes.txt.")
    try:  # informational only: never fail the check over a lease lookup
        lease = next((l for l in api.get("lease", params={"hardware": mac}, fields=["binding_state"])
                      if l.get("binding_state") == "ACTIVE"), None)
    except WapiError:
        lease = None
    if lease:
        print(f"  (the new device holds a lease on {lease['address']})")


STAGES = {"baseline": stage_baseline, "scopes": stage_scopes, "leases": stage_leases,
          "macs": stage_macs, "reservation": stage_reservation}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage", required=True, choices=sorted(STAGES))
    parser.add_argument("--skip-hosts", action="store_true",
                        help="baseline: do not probe the Windows servers and the simulator (offline tests)")
    args = parser.parse_args()

    REASON_FILE.unlink(missing_ok=True)
    try:
        api = NiosWapi.from_env()
        api.detect_version()
        if args.stage == "baseline":
            stage_baseline(api, check_hosts=not args.skip_hosts)
        else:
            STAGES[args.stage](api)
    except Check as exc:
        REASON_FILE.write_text(str(exc))
        print(f"  [FAIL] {exc}")
        return 1
    except WapiError as exc:
        REASON_FILE.write_text(f"Could not talk to the Grid Master: {exc}")
        print(f"  [FAIL] {exc}")
        return 1
    print(f"  [OK] stage '{args.stage}' verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
