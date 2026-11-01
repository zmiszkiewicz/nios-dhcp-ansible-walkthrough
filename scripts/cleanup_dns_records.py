#!/usr/bin/env python3
"""Remove the lab's public DNS records. Safe to run when they are already gone.

The addresses are read back from Route 53, so cleanup does not depend on the
Terraform state or on the environment variables still holding the old values.
Same environment as setup_dns.py, minus the three addresses.
"""
from __future__ import annotations

import sys

from setup_dns import required, route53


def find_record(client, zone_id, name):
    """The A record called `name`, or None. Route 53 lists from the name given."""
    listing = client.list_resource_record_sets(
        HostedZoneId=zone_id, StartRecordName=name, StartRecordType="A", MaxItems="1")
    for record in listing["ResourceRecordSets"]:
        if record["Name"] == name and record["Type"] == "A":
            return record
    return None


def main():
    client, zone_id = route53(), required("DEMO_HOSTED_ZONE_ID")
    participant, zone = required("INSTRUQT_PARTICIPANT_ID"), required("DEMO_ZONE_NAME")

    to_delete = []
    for suffix in ("infoblox", "wakeup1", "wakeup2"):
        record = find_record(client, zone_id, f"{participant}-{suffix}.{zone}.")
        if record:
            to_delete.append({"Action": "DELETE", "ResourceRecordSet": record})

    if not to_delete:
        print("  no lab DNS records found; nothing to remove")
        return 0
    client.change_resource_record_sets(
        HostedZoneId=zone_id,
        ChangeBatch={"Comment": "NIOS DHCP + Ansible walkthrough lab cleanup", "Changes": to_delete},
    )
    for change in to_delete:
        print(f"  [OK] deleted {change['ResourceRecordSet']['Name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
