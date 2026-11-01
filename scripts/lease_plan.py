#!/usr/bin/env python3
"""Work out, from the grid itself, which clients should ask which member for a lease.

Reads every DHCP range on the grid and prints one tab-separated line per scope:

  scope_index  network  relay_ip  server_ip  count  hostname_prefix

  scope_index      1-based position of the network in sorted order (stable, and
                   used to build the client MACs)
  relay_ip         the simulator's address in that branch (giaddr)
  server_ip        LAN1 address of the member the range is assigned to
  hostname_prefix  taken from the network's Branch extensible attribute

bring_clients_online.sh runs lease_gen.py on the simulator for each line, so the
scope-to-member assignment is read from NIOS rather than repeated in a second file.
"""
from __future__ import annotations

import json
import sys

import lab_config as cfg
from nios_wapi import NiosWapi, WapiError, flatten_ea


def build_plan(api, count=None):
    count = count or cfg.CLIENTS_PER_SCOPE
    members = {m["host_name"]: (m.get("vip_setting") or {}).get("address")
               for m in api.get("member", fields=["vip_setting"])}
    networks = {}
    for rng in api.get("range", fields=["network", "member", "server_association_type"]):
        member = rng.get("member") or {}
        if rng.get("server_association_type") != "MEMBER" or not member.get("name"):
            continue
        server_ip = members.get(member["name"]) or member.get("ipv4addr")
        if not server_ip:
            raise WapiError(f"cannot find the LAN1 address of member {member['name']}")
        networks.setdefault(rng["network"], server_ip)

    plan = []
    for index, network in enumerate(sorted(networks, key=lambda n: tuple(int(o) for o in n.split("/")[0].split("."))), 1):
        net_obj = api.first("network", params={"network": network}, fields=["extattrs"]) or {}
        branch = flatten_ea(net_obj.get("extattrs")).get("Branch") or f"scope{index}"
        plan.append({
            "scope_index": index,
            "network": network,
            "relay_ip": cfg.relay_for(network),
            "server_ip": networks[network],
            "count": count,
            "hostname_prefix": branch.lower().replace("-", "") + "-pc",
        })
    return plan


def main(argv):
    api = NiosWapi.from_env()
    api.detect_version()
    try:
        plan = build_plan(api)
    except WapiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if not plan:
        print("ERROR: no DHCP range is assigned to a member yet. Run the create_scopes playbook first.",
              file=sys.stderr)
        return 1
    if "--json" in argv:
        print(json.dumps(plan, indent=2))
    else:
        for item in plan:
            print("\t".join(str(item[k]) for k in
                            ("scope_index", "network", "relay_ip", "server_ip", "count", "hostname_prefix")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
