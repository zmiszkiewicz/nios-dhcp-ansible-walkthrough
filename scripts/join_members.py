#!/usr/bin/env python3
"""Create the grid member(s) on the Grid Master and bring them into service.

Two phases, because a member can only be built once the Grid Master has issued it
a join token:

  python3 join_members.py prepare   Phase 1. On the Grid Master: add each member
                                    as an offline, pre-provisioned VNIOS member,
                                    ask for its join token, download the Grid
                                    Master's certificate, and write
                                    terraform/01-members/members.auto.tfvars.json
                                    (the members' cloud-init user data).
  python3 join_members.py wait      Phase 2, after `terraform apply` in
                                    01-members. Wait for each member to join and
                                    come up, then turn its DHCP service on.

The flow follows the offline-member / join-token / cloud-init sequence of the
older GMC script in the ad-migration lab, with the hard-coded password and
hostnames replaced by configuration, and the join address changed to the Grid
Master's private LAN1 (the members sit in the same VPC).

Reads INFOBLOX_HOST, INFOBLOX_PASSWORD, ... (see nios_wapi.py) and the member
names and addresses in lab_config.py.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import lab_config as cfg
from nios_wapi import NiosWapi, WapiError, log, wait_until

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "terraform" / "01-members" / "members.auto.tfvars.json"


def find_grid_master(api):
    """The member whose VIP is the Grid Master's LAN1 (falls back to the first member)."""
    members = api.get("member", fields=["vip_setting"])
    for member in members:
        if (member.get("vip_setting") or {}).get("address") == cfg.GM_LAN1_IP:
            return member
    if not members:
        raise WapiError("the grid has no members, not even the Grid Master")
    return members[0]


def cloud_init(member, token, certificate_pem, admin_password):
    """The #infoblox-config user data that makes a booting vNIOS join the grid."""
    lines = [
        "#infoblox-config",
        f"temp_license: {cfg.MEMBER_TEMP_LICENSE}",
        "remote_console_enabled: y",
        # JSON string syntax is valid YAML, so quotes and backslashes are safe.
        f"default_admin_password: {json.dumps(admin_password)}",
        "lan1:",
        f"  v4_addr: {member['ip']}",
        f"  v4_netmask: {cfg.GRID_NETMASK}",
        f"  v4_gw: {cfg.GRID_GATEWAY}",
        "gridmaster:",
        f"  ip_addr: {cfg.GM_LAN1_IP}",
        f"  token: {token}",
        "  certificate: |",
    ]
    lines += [f"    {line}" for line in certificate_pem.strip().splitlines()]
    return "\n".join(lines) + "\n"


def download_gm_certificate(api, gm_name):
    info = api.post(
        "fileop",
        {"certificate_usage": "ADMIN", "member": gm_name},
        params={"_function": "downloadcertificate"},
    )
    parsed = urlparse(info["url"])
    # The URL names the Grid Master by an internal address; fetch it from the
    # address we are already talking to.
    url = f"{api.scheme}://{api.host}{parsed.path}"
    if parsed.query:
        url += f"?{parsed.query}"
    return api.raw_get(url)


def prepare(api, out_path):
    password = os.environ["INFOBLOX_PASSWORD"]
    gm = find_grid_master(api)
    log(f"Grid Master is {gm['host_name']} ({cfg.GM_LAN1_IP})")
    certificate = download_gm_certificate(api, gm["host_name"])
    log("downloaded the Grid Master certificate")

    user_data = {}
    for member in cfg.MEMBERS:
        existing = api.first("member", params={"host_name": member["name"]})
        if existing:
            ref = existing["_ref"]
            log(f"{member['name']} already exists on the grid")
        else:
            ref = api.post("member", {
                "host_name": member["name"],
                "platform": "VNIOS",
                "vip_setting": {
                    "address": member["ip"],
                    "subnet_mask": cfg.GRID_NETMASK,
                    "gateway": cfg.GRID_GATEWAY,
                },
            })
            api.put(ref, {"pre_provisioning": {
                "licenses": cfg.MEMBER_LICENSES,
                "hardware_info": [{"hwtype": cfg.MEMBER_HWTYPE}],
            }})
            log(f"added {member['name']} as an offline {cfg.MEMBER_HWTYPE} member")
        token = api.call(ref, "create_token")["pnode_tokens"][0]["token"]
        user_data[member["key"]] = cloud_init(member, token, certificate, password)
        log(f"issued a join token for {member['name']}")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"member_cloud_init": user_data}, indent=2))
    out_path.chmod(0o600)
    log(f"wrote {out_path}")
    return 0


def service_statuses(member_obj):
    """[(service, status)] from a member's node_info, tolerant of missing fields."""
    found = []
    for node in member_obj.get("node_info") or []:
        for item in node.get("service_status") or []:
            found.append((item.get("service"), item.get("status")))
    return found


def wait_for_member(api, member, timeout, interval):
    seen = {"last": None}

    def joined():
        obj = api.first("member", params={"host_name": member["name"]}, fields=["node_info"])
        if not obj:
            return False
        statuses = service_statuses(obj)
        # Print what the grid reports whenever it changes. The first live boot is
        # the first time these status values are seen, so show them.
        if statuses != seen["last"]:
            print(f"  {member['name']} service status: {statuses or 'none reported yet'}")
            seen["last"] = statuses
        return any(status == "WORKING" for _, status in statuses)

    return wait_until(joined, timeout, interval, f"{member['name']} to join the grid")


def enable_dhcp(api, member):
    for props in api.get("member:dhcpproperties", fields=["enable_dhcp"]):
        if props.get("host_name") == member["name"]:
            api.put(props["_ref"], {"enable_dhcp": True})
            return True
    return False


def wait_dhcp_working(api, member, timeout, interval):
    def working():
        obj = api.first("member", params={"host_name": member["name"]}, fields=["node_info"])
        return any(service == "DHCP" and status == "WORKING"
                   for service, status in service_statuses(obj or {}))
    return wait_until(working, timeout, interval, f"DHCP to be WORKING on {member['name']}")


def wait(api, timeout, interval):
    failures = 0
    for member in cfg.MEMBERS:
        if not wait_for_member(api, member, timeout, interval):
            log(f"{member['name']} did not join within {timeout}s", ok=False)
            print("         Check the instance system log in the AWS console; the join token and")
            print("         certificate in its user data must match this grid. See the README.")
            failures += 1
            continue
        log(f"{member['name']} has joined the grid")
        if enable_dhcp(api, member):
            log(f"DHCP service enabled on {member['name']}")
        else:
            log(f"no member:dhcpproperties object for {member['name']}", ok=False)
            failures += 1
            continue
        if not wait_dhcp_working(api, member, 300, interval):
            # Not fatal: the status vocabulary is confirmed only on a live grid, and
            # the lease check that follows is the real proof the service works.
            print(f"  (could not confirm DHCP WORKING on {member['name']} from node_info; continuing)")
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_prepare = sub.add_parser("prepare", help="create members and write their cloud-init")
    p_prepare.add_argument("--out", default=str(DEFAULT_OUT))
    p_wait = sub.add_parser("wait", help="wait for members to join, then enable DHCP")
    p_wait.add_argument("--timeout", type=int, default=1200)
    p_wait.add_argument("--interval", type=int, default=20)
    args = parser.parse_args()

    api = NiosWapi.from_env()
    api.detect_version()
    try:
        if args.command == "prepare":
            return prepare(api, args.out)
        return wait(api, args.timeout, args.interval)
    except WapiError as exc:
        log(str(exc), ok=False)
        return 1


if __name__ == "__main__":
    sys.exit(main())
