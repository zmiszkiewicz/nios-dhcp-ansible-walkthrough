#!/usr/bin/env python3
"""Publish public names for the Grid Manager tab and the two wake-up desktops.

The track's browser tab opens https://${_SANDBOX_ID}-infoblox.<zone>, and the
Windows desktop tab connects to <participant>-wakeup1.<zone> and -wakeup2.<zone>.
This creates those records in the shared demo zone. Credentials come from
Instruqt secrets via the environment; the zone name is supplied by the track, not
stored in this repo.

  python3 setup_dns.py            create or update the records
  python3 setup_dns.py --delete   remove them (cleanup_dns_records.py calls this)

Required environment:
  DEMO_AWS_ACCESS_KEY_ID  DEMO_AWS_SECRET_ACCESS_KEY  DEMO_HOSTED_ZONE_ID
  DEMO_ZONE_NAME          zone the hosted zone serves
  INSTRUQT_PARTICIPANT_ID
  GM_IP  WAKEUP1_IP  WAKEUP2_IP     public addresses (from the Terraform outputs)
"""
from __future__ import annotations

import os
import sys

import boto3


def required(name):
    value = os.getenv(name)
    if not value:
        sys.exit(f"ERROR: {name} is not set")
    return value


def route53():
    session = boto3.Session(
        aws_access_key_id=required("DEMO_AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=required("DEMO_AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("DEMO_AWS_REGION", "us-east-1"),
    )
    return session.client("route53")


def records():
    participant, zone = required("INSTRUQT_PARTICIPANT_ID"), required("DEMO_ZONE_NAME")
    return {
        f"{participant}-infoblox.{zone}.": required("GM_IP"),
        f"{participant}-wakeup1.{zone}.": required("WAKEUP1_IP"),
        f"{participant}-wakeup2.{zone}.": required("WAKEUP2_IP"),
    }


def change(action):
    client, zone_id = route53(), required("DEMO_HOSTED_ZONE_ID")
    wanted = records()
    changes = [{
        "Action": action,
        "ResourceRecordSet": {"Name": name, "Type": "A", "TTL": 60,
                              "ResourceRecords": [{"Value": ip}]},
    } for name, ip in wanted.items()]
    client.change_resource_record_sets(
        HostedZoneId=zone_id,
        ChangeBatch={"Comment": "NIOS DHCP + Ansible walkthrough lab", "Changes": changes},
    )
    for name, ip in wanted.items():
        print(f"  [OK] {action.lower()} {name} -> {ip}")


def main(argv):
    change("DELETE" if "--delete" in argv else "UPSERT")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
