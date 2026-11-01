"""Small filters the lab playbooks use, so the playbooks stay readable.

Each one is a few lines of plain Python (standard library only) and is covered by
scripts/tests/test_filters.py.
"""
from __future__ import annotations

import ipaddress
import re

from ansible.errors import AnsibleFilterError

MAC_RE = re.compile(r"^([0-9a-f]{2})([:-])(?:[0-9a-f]{2}\2){4}[0-9a-f]{2}$", re.IGNORECASE)


def _ip_of(value):
    """'10.1.2.0/24' and '10.1.2.5' both give an address object."""
    return ipaddress.ip_address(str(value).split("/")[0])


def sort_by_ip(items, attribute="address"):
    """Sort dicts by an address attribute numerically (a plain sort puts .100 before .20)."""
    return sorted(items, key=lambda item: _ip_of(item[attribute]))


def containing_network(networks, ip, attribute="network"):
    """The most specific network in `networks` (dicts) that contains `ip`, or None."""
    address = ipaddress.ip_address(ip)
    best = None
    for item in networks:
        net = ipaddress.ip_network(item[attribute], strict=False)
        if address in net and (best is None or net.prefixlen > best[0].prefixlen):
            best = (net, item)
    return best[1] if best else None


def normalize_mac(mac):
    """Lower-case, colon-separated MAC. Accepts the dash form Microsoft DHCP uses."""
    if not MAC_RE.match(str(mac)):
        raise AnsibleFilterError(f"not a MAC address: {mac!r} (expected aa:bb:cc:dd:ee:ff)")
    return str(mac).lower().replace("-", ":")


def ms_clientid(mac):
    """The form Get-DhcpServerv4Lease gives for ClientId: lower-case, dash-separated."""
    return normalize_mac(mac).replace(":", "-")


def convention_wakeup(network, last_octet=111):
    """The original script's rule: first three octets of the scope, then .111."""
    octets = str(network).split("/")[0].split(".")
    if len(octets) != 4:
        raise AnsibleFilterError(f"not an IPv4 network: {network!r}")
    return ".".join(octets[:3] + [str(last_octet)])


def group_macs(plan):
    """Group a scope plan by the inventory host that receives the MAC list.

    Scopes sharing a wake-up server contribute to one list (de-duplicated, in
    scope order). Scopes whose server is not in the inventory come back as
    `unmapped` so the playbook can say so instead of failing quietly.
    """
    hosts, unmapped = {}, []
    for item in plan:
        host = item.get("wakeup_host")
        if not host:
            unmapped.append(item)
            continue
        entry = hosts.setdefault(host, {"macs": [], "scopes": []})
        entry["scopes"].append(item["network"])
        entry["macs"].extend(mac for mac in item["macs"] if mac not in entry["macs"])
    return {"hosts": hosts, "unmapped": unmapped}


def table(items, columns):
    """Format dicts as aligned text lines. columns: [{'title': 'NETWORK', 'key': 'network'}, ...]."""
    rows = [[str(item.get(col["key"], "")) for col in columns] for item in items]
    titles = [col["title"] for col in columns]
    widths = [max(len(titles[i]), *(len(row[i]) for row in rows)) if rows else len(titles[i])
              for i in range(len(columns))]

    def fmt(cells):
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells)).rstrip()

    return [fmt(titles), fmt(["-" * w for w in widths])] + [fmt(row) for row in rows]


class FilterModule:
    def filters(self):
        return {
            "lab_sort_by_ip": sort_by_ip,
            "lab_containing_network": containing_network,
            "lab_normalize_mac": normalize_mac,
            "lab_ms_clientid": ms_clientid,
            "lab_convention_wakeup": convention_wakeup,
            "lab_group_macs": group_macs,
            "lab_table": table,
        }
