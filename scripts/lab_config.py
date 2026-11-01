#!/usr/bin/env python3
"""Lab constants shared by the scripts, in one place.

Everything here has a default that matches the Terraform in this repo, and can be
overridden from the environment (the track's setup script exports the overrides).
Playbook data lives in ansible/group_vars/all/scopes.yml; keep the member names
and addresses in step with it and with terraform/01-members/variables.tf.

  python3 lab_config.py get <key>     print one value (used by the shell scripts)
"""
from __future__ import annotations

import os
import sys


def _env(name, default):
    return os.getenv(name) or default


# --- The grid ---------------------------------------------------------------
GM_LAN1_IP = _env("GM_LAN1_IP", "10.100.0.11")      # the grid VIP members join
GRID_NETMASK = _env("GRID_NETMASK", "255.255.255.0")
GRID_GATEWAY = _env("GRID_GATEWAY", "10.100.0.1")

# One member serves all three branches (the vCPU budget allows one 4-vCPU member). To add a
# second, add an entry here (name nios-member2.lab.test, ip 10.100.0.22), a member2 entry in
# terraform/01-members/variables.tf and in ansible/inventory/group_vars/all/scopes.yml.
MEMBERS = [
    {"key": "member1", "name": _env("NIOS_MEMBER1_NAME", "nios-member1.lab.test"),
     "ip": _env("NIOS_MEMBER1_IP", "10.100.0.21")},
]

# The hardware model a member is pre-provisioned as, and the matching temporary
# license written into its cloud-init. The same model as the Grid Master (IB-V825),
# which is known to run on this AMI at the member's size (m5.xlarge).
MEMBER_HWTYPE = _env("NIOS_MEMBER_HWTYPE", "IB-V825")
MEMBER_LICENSES = ["dns", "dhcp", "enterprise", "nios"]
MEMBER_TEMP_LICENSE = _env(
    "NIOS_MEMBER_TEMP_LICENSE", f"nios {MEMBER_HWTYPE} enterprise dns dhcp cloud")

# --- Branch simulator -------------------------------------------------------
# The simulator owns <first three octets of the branch>.<SIM_HOST_OCTET> in every
# branch subnet. That address is the DHCP relay address (giaddr) for the branch.
SIM_HOST_OCTET = int(_env("SIM_HOST_OCTET", "20"))
CLIENTS_PER_SCOPE = int(_env("CLIENTS_PER_SCOPE", "30"))
SIM_DIR = "/opt/lab"                                  # where lease_gen.py is copied to

# --- The branch printer -----------------------------------------------------
PRINTER_IP = _env("PRINTER_IP", "10.100.11.60")
PRINTER_NAME = _env("PRINTER_NAME", "printer-br1001")
PRINTER_OLD_MAC = _env("PRINTER_OLD_MAC", "02:00:00:01:f0:01")
PRINTER_NEW_MAC = _env("PRINTER_NEW_MAC", "02:00:00:01:f0:02")

# --- Windows wake-up servers ------------------------------------------------
WAKEUP_USER = _env("WINDOWS_ADMIN_USER", "Administrator")
WAKEUP_FILE = r"C:\Windows\System32\mac.txt"


def relay_for(network):
    """Relay (giaddr) address the simulator uses for a network like 10.100.11.0/24."""
    octets = network.split("/")[0].split(".")
    return ".".join(octets[:3] + [str(SIM_HOST_OCTET)])


def mac_for(scope_index, client_index):
    """Deterministic MAC for the n-th client of the n-th scope (1-based).

    02:... is a locally administered unicast address, so these can never collide
    with a real device. The same input always gives the same MAC, which makes a
    second run renew the same leases instead of creating new ones.
    """
    return f"02:00:00:{scope_index:02x}:00:{client_index:02x}"


def main(argv):
    if len(argv) == 3 and argv[1] == "get":
        value = globals().get(argv[2].upper())
        if value is None or isinstance(value, (list, dict)):
            print(f"unknown or non-scalar key: {argv[2]}", file=sys.stderr)
            return 1
        print(value)
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
