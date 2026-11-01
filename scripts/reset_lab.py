#!/usr/bin/env python3
"""Put the lab back to the start of a challenge so it can be run again.

  python3 reset_lab.py 2|3|4|5|all

  2  remove the branch networks, ranges, leases, the printer reservation and the
     extensible attribute definitions (back to a bare grid with two members)
  3  delete the exported files in ansible/out
  4  remove mac.txt and C:\\legacy from the wake-up servers
  5  put the printer's reservation back on its original MAC and clear the logs

`all` runs 5, 4, 3, 2 in that order. The grid, the members and the VMs are left
alone. reset_stage.sh is the usual way to call this.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import yaml

import lab_config as cfg
from nios_wapi import NiosWapi, WapiError, load_state, log
from winrm_helper import WinRMError, run_ps

REPO_ROOT = Path(__file__).resolve().parent.parent
SCOPES = REPO_ROOT / "ansible" / "inventory" / "group_vars" / "all" / "scopes.yml"


def scope_data():
    return yaml.safe_load(SCOPES.read_text())


def reset_2(api):
    data = scope_data()
    for fixed in api.get("fixedaddress", params={"ipv4addr": cfg.PRINTER_IP}):
        api.delete(fixed["_ref"])
        log(f"deleted the reservation on {cfg.PRINTER_IP}")
    for scope in data["scopes"]:
        for rng in api.get("range", params={"network": scope["network"]}):
            api.delete(rng["_ref"])
        for net in api.get("network", params={"network": scope["network"]}):
            api.delete(net["_ref"])
            log(f"deleted network {scope['network']} and its range")
    for ea in data["ea_definitions"]:
        for definition in api.get("extensibleattributedef", params={"name": ea["name"]}):
            api.delete(definition["_ref"])
            log(f"deleted the {ea['name']} extensible attribute")


def reset_3(_api):
    out = REPO_ROOT / "ansible" / "out"
    if out.exists():
        shutil.rmtree(out)
        log("removed ansible/out")


def reset_4(_api):
    for name, server in load_state("lab").get("wakeup_servers", {}).items():
        try:
            run_ps(server["public_ip"],
                   f"Remove-Item -Force -ErrorAction SilentlyContinue '{cfg.WAKEUP_FILE}'; "
                   "Remove-Item -Recurse -Force -ErrorAction SilentlyContinue 'C:\\legacy'",
                   timeout=60)
            log(f"cleared mac.txt and C:\\legacy on {name}")
        except WinRMError as exc:
            log(f"could not reach {name}: {exc}", ok=False)


def reset_5(api):
    scopes = scope_data()["scopes"]
    for fixed in api.get("fixedaddress", params={"ipv4addr": cfg.PRINTER_IP}):
        api.delete(fixed["_ref"])
    if api.first("network", params={"network": scopes[0]["network"]}):
        api.post("fixedaddress", {
            "name": cfg.PRINTER_NAME, "ipv4addr": cfg.PRINTER_IP, "mac": cfg.PRINTER_OLD_MAC,
            "network": scopes[0]["network"], "comment": "branch printer"})
        log(f"{cfg.PRINTER_IP} is reserved for {cfg.PRINTER_OLD_MAC} again")
    logs = REPO_ROOT / "logs"
    if logs.exists():
        shutil.rmtree(logs)
        log("cleared logs/")


STEPS = {"2": reset_2, "3": reset_3, "4": reset_4, "5": reset_5}


def main(argv):
    if len(argv) != 1 or argv[0] not in ("2", "3", "4", "5", "all"):
        print(__doc__)
        return 1
    steps = ["5", "4", "3", "2"] if argv[0] == "all" else [argv[0]]
    api = NiosWapi.from_env()
    api.detect_version()
    try:
        for step in steps:
            print(f"=== reset {step} ===")
            STEPS[step](api)
    except WapiError as exc:
        log(str(exc), ok=False)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
