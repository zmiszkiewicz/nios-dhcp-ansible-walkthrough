"""The same facts live in Terraform, Ansible variables and Python. Keep them in step.

Addresses and names are written down in a few places on purpose (each tool reads
its own file). These tests fail if the copies drift apart.
"""
import ipaddress
import re
from pathlib import Path

import yaml

import lab_config as cfg

REPO = Path(__file__).resolve().parents[2]
VARS = REPO / "ansible" / "inventory" / "group_vars" / "all"


def scopes_yml():
    return yaml.safe_load((VARS / "scopes.yml").read_text())


def terraform_branches():
    text = (REPO / "terraform" / "00-baseline" / "network.tf").read_text()
    rows = re.findall(r'(br\d)\s*=\s*\{\s*cidr\s*=\s*"([^"]+)",\s*sim_ip\s*=\s*"([^"]+)",\s*wakeup_ip\s*=\s*(null|"[^"]+")', text)
    return {name: {"cidr": cidr, "sim_ip": sim, "wakeup_ip": None if wake == "null" else wake.strip('"')}
            for name, cidr, sim, wake in rows}


def test_member_names_and_addresses_agree():
    text = (VARS / "scopes.yml").read_text()
    defaults = dict(re.findall(r"NIOS_MEMBER(\d)_NAME'\) \| default\('([^']+)'", text))
    assert defaults == {str(i): m["name"] for i, m in enumerate(cfg.MEMBERS, 1)}

    tf = (REPO / "terraform" / "01-members" / "variables.tf").read_text()
    ips = dict(re.findall(r'(member\d)\s*=\s*\{\s*ip\s*=\s*"([^"]+)"', tf))
    assert ips == {m["key"]: m["ip"] for m in cfg.MEMBERS}

    grid_net = ipaddress.ip_network("10.100.0.0/24")
    assert all(ipaddress.ip_address(m["ip"]) in grid_net for m in cfg.MEMBERS)
    assert ipaddress.ip_address(cfg.GM_LAN1_IP) in grid_net


def test_printer_settings_agree():
    text = (VARS / "scopes.yml").read_text()
    for var, want in (("PRINTER_IP", cfg.PRINTER_IP), ("PRINTER_NAME", cfg.PRINTER_NAME),
                      ("PRINTER_OLD_MAC", cfg.PRINTER_OLD_MAC)):
        assert re.search(rf"{var}'\) \| default\('{re.escape(want)}'", text), var

    first = scopes_yml()["scopes"][0]
    assert ipaddress.ip_address(cfg.PRINTER_IP) in ipaddress.ip_network(first["network"])
    # the reservation sits outside the dynamic range, so a pool address can never collide with it
    assert not (ipaddress.ip_address(first["range_start"]) <= ipaddress.ip_address(cfg.PRINTER_IP)
                <= ipaddress.ip_address(first["range_end"]))

    sol = yaml.safe_load((REPO / "solutions" / "05-reservation" / "replace_reservation.vars.yml").read_text())
    assert sol == {"ip": cfg.PRINTER_IP, "mac": cfg.PRINTER_NEW_MAC}


def test_terraform_branches_match_the_scopes():
    branches = terraform_branches()
    scopes = scopes_yml()["scopes"]
    assert sorted(b["cidr"] for b in branches.values()) == sorted(s["network"] for s in scopes)
    for branch in branches.values():
        assert branch["sim_ip"] == cfg.relay_for(branch["cidr"])          # the relay address the plan computes
        if branch["wakeup_ip"]:
            assert branch["wakeup_ip"].rsplit(".", 1)[0] == branch["cidr"].split("/")[0].rsplit(".", 1)[0]
            assert branch["wakeup_ip"].endswith(".111")


def test_every_wakeup_slot_points_at_a_real_server_or_uses_the_convention():
    branches = terraform_branches()
    servers = {b["wakeup_ip"] for b in branches.values() if b["wakeup_ip"]}
    uses_convention = 0
    for scope in scopes_yml()["scopes"]:
        value = scope["extattrs"].get("WakeUp_Server")
        if value is None:
            uses_convention += 1
            value = scope["network"].rsplit(".", 1)[0] + ".111"
        assert value in servers, f"{scope['network']} would push to {value}, which no VM owns"
    assert uses_convention >= 1, "the lab should show the .111 fallback on at least one scope"


def test_ranges_sit_inside_their_networks_and_use_known_members():
    keys = {m["key"] for m in cfg.MEMBERS}
    for scope in scopes_yml()["scopes"]:
        net = ipaddress.ip_network(scope["network"])
        assert ipaddress.ip_address(scope["range_start"]) in net
        assert ipaddress.ip_address(scope["range_end"]) in net
        assert scope["member"] in keys
        assert "Branch" in scope["extattrs"]


def test_extensible_attributes_defined_cover_those_used():
    scopes = scopes_yml()
    defined = {e["name"] for e in scopes["ea_definitions"]}
    used = {k for s in scopes["scopes"] for k in s["extattrs"]}
    assert used <= defined


def test_clients_fit_the_pool():
    for scope in scopes_yml()["scopes"]:
        size = int(ipaddress.ip_address(scope["range_end"])) - int(ipaddress.ip_address(scope["range_start"])) + 1
        assert cfg.CLIENTS_PER_SCOPE <= size


def test_vcpu_budget():
    """14 vCPU: the AWS sandbox quota is 16 and the other labs already use it all."""
    vcpu = {"m5.xlarge": 4, "m5.large": 2, "t3.medium": 2, "t3.small": 2}
    base = (REPO / "terraform" / "00-baseline" / "variables.tf").read_text()
    members = (REPO / "terraform" / "01-members" / "variables.tf").read_text()

    def default(text, name):
        return re.search(rf'variable "{name}".*?default\s*=\s*"([^"]+)"', text, re.S).group(1)

    total = (vcpu[default(base, "gm_instance_type")]
             + len(cfg.MEMBERS) * vcpu[default(members, "member_instance_type")]
             + 2 * vcpu[default(base, "windows_instance_type")]
             + vcpu[default(base, "sim_instance_type")])
    assert total == 14


def test_security_group_descriptions_use_only_characters_aws_accepts():
    """AWS rejects a security group or rule description outside this set, at plan time
    (an apostrophe did once). terraform validate does not catch it."""
    allowed = re.compile(r"^[0-9A-Za-z_ .:/()#,@\[\]+=&;{}!$*-]*$")
    bad = []
    for tf in (REPO / "terraform").rglob("*.tf"):
        text = tf.read_text()
        for block in re.finditer(r'resource "aws_security_group(?:_rule)?"[^\n]*\{(.*?)\n\}\n', text, re.S):
            for desc in re.findall(r'^\s*description\s*=\s*"([^"]*)"', block.group(1), re.M):
                if not allowed.match(desc):
                    bad.append((tf.name, desc))
    assert not bad, bad
