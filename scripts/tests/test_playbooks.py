"""Run the real playbooks, with the real infoblox.nios_modules collection, against the fake grid.

Needs ansible-playbook and the pinned collections (ansible-galaxy collection
install -r ansible/requirements.yml). Skipped when they are missing. The Windows
playbooks (push_macs, compare_legacy) cannot run here; they are syntax-checked.
What this proves is that the playbooks drive the modules correctly and compute
the right files; it does not prove NIOS or Windows behave the same way.
"""
import os
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import lab_config as cfg
import verify_lab

REPO = Path(__file__).resolve().parents[2]
BIN = Path(sys.executable).parent
ANSIBLE = shutil.which("ansible-playbook", path=f"{BIN}{os.pathsep}{os.environ['PATH']}")


def collections_ready():
    if not ANSIBLE:
        return False
    listing = subprocess.run([str(BIN / "ansible-galaxy") if (BIN / "ansible-galaxy").exists() else "ansible-galaxy",
                              "collection", "list"], capture_output=True, text=True, check=False,
                             env=os.environ).stdout
    return "infoblox.nios_modules" in listing and "ansible.windows" in listing


pytestmark = pytest.mark.skipif(not collections_ready(), reason="ansible-playbook or the collections are missing")


@pytest.fixture
def lab(grid, tmp_path):
    """A private copy of the repo (playbooks write ansible/out and logs/), plus a runner."""
    target = tmp_path / "repo"
    shutil.copytree(REPO, target, ignore=shutil.ignore_patterns(
        ".git", ".state", ".terraform*", "*.tfstate*", "__pycache__", "out", "logs", ".pytest_cache"))
    env = dict(os.environ, OBJC_DISABLE_INITIALIZE_FORK_SAFETY="YES", ANSIBLE_NOCOLOR="1",
               PATH=f"{BIN}{os.pathsep}{os.environ['PATH']}")

    def play(name, *extra, check=True):
        result = subprocess.run([ANSIBLE, f"playbooks/{name}.yml", *extra], cwd=target / "ansible",
                                env=env, capture_output=True, text=True, check=False)
        if check and result.returncode != 0:
            pytest.fail(f"{name} failed:\n{result.stdout[-3000:]}\n{result.stderr[-1500:]}")
        return result

    play.repo = target
    play.grid = grid
    return play


def changed(result):
    return int(re.search(r"changed=(\d+)", result.stdout).group(1))


def seed_leases(grid, per_scope=12):
    random.seed(7)
    for index, net in enumerate(("10.100.11", "10.100.12", "10.100.13"), 1):
        for n, last in enumerate(random.sample(range(100, 201), per_scope), 1):   # shuffled on purpose
            grid.grid.add_lease(f"{net}.0/24", f"{net}.{last}", cfg.mac_for(index, n))
        grid.grid.add_lease(f"{net}.0/24", f"{net}.250", "02:de:ad:00:00:01", state="EXPIRED")


def test_every_playbook_passes_syntax_check(lab):
    for name in ("create_scopes", "collect_macs", "push_macs", "compare_legacy", "replace_reservation", "seed_printer"):
        assert lab(name, "--syntax-check", check=False).returncode == 0, name


def test_create_scopes_builds_the_grid_and_is_idempotent(lab):
    first = lab("create_scopes")
    assert changed(first) >= 1
    assert changed(lab("create_scopes")) == 0                 # a second run changes nothing

    grid = lab.grid.grid
    assert {e["name"] for e in grid.of_type("extensibleattributedef")} == {"Branch", "Site", "WakeUp_Server"}
    nets = {n["network"]: n for n in grid.of_type("network")}
    assert set(nets) == {"10.100.11.0/24", "10.100.12.0/24", "10.100.13.0/24"}
    assert nets["10.100.12.0/24"]["extattrs"]["WakeUp_Server"] == {"value": "10.100.12.111"}
    assert "WakeUp_Server" not in nets["10.100.11.0/24"]["extattrs"]
    ranges = {r["network"]: r for r in grid.of_type("range")}
    assert all(n["members"] == [{"_struct": "dhcpmember", "name": cfg.MEMBERS[0]["name"]}] for n in nets.values())
    assert ranges["10.100.11.0/24"]["member"] == {"_struct": "dhcpmember", "name": cfg.MEMBERS[0]["name"]}
    assert all(r["member"]["name"] == cfg.MEMBERS[0]["name"] for r in ranges.values())
    assert ranges["10.100.13.0/24"]["server_association_type"] == "MEMBER"
    # and the lab's own check agrees
    from nios_wapi import NiosWapi
    api = NiosWapi.from_env()
    api.detect_version()
    verify_lab.stage_scopes(api)


def test_collect_macs_matches_an_independent_calculation(lab):
    lab("create_scopes")
    seed_leases(lab.grid)
    result = lab("collect_macs")
    assert "36 active leases across 3 scopes" in result.stdout
    assert "convention (.111)" in result.stdout and "overrides .111" in result.stdout

    out = lab.repo / "ansible" / "out"
    from nios_wapi import NiosWapi
    api = NiosWapi.from_env()
    api.detect_version()
    expected = verify_lab.expected_wakeup_files(api)

    # one scope per server (branch 1): the per-scope file is exactly what the server should hold
    assert (out / "10.100.11.0.mac.txt").read_bytes() == expected["10.100.11.111"]
    # two scopes share a server (branches 2 and 3): the server's file is both lists, one header
    scope2 = (out / "10.100.12.0.mac.txt").read_bytes()
    scope3 = (out / "10.100.13.0.mac.txt").read_bytes()
    assert expected["10.100.12.111"] == scope2 + scope3[len(b"\r\n\r\n"):]
    # MS-format export has the header the original script strips
    assert (out / "10.100.11.0.csv").read_bytes().startswith(b'#TYPE Selected.Microsoft.Management.Infrastructure.CimInstance\r\n"ClientId"\r\n')
    # the expired lease is excluded, and MACs come out in IP order (not MAC or arrival order)
    assert b"02:de:ad" not in scope2
    active = [l for l in lab.grid.grid.of_type("lease")
              if l["network"] == "10.100.11.0/24" and l["binding_state"] == "ACTIVE"]
    by_ip = sorted(active, key=lambda l: tuple(int(o) for o in l["address"].split(".")))
    written = (out / "10.100.11.0.mac.txt").read_bytes().decode().split("\r\n")[2:-1]
    assert written == [l["hardware"] for l in by_ip]
    assert written != sorted(written)      # guards the test itself: the seed order really was shuffled


def test_collect_macs_explains_itself_when_there_are_no_leases(lab):
    lab("create_scopes")
    result = lab("collect_macs", check=False)
    assert result.returncode != 0
    assert "bring_clients_online.sh" in result.stdout


def test_replace_reservation_workflow(lab):
    lab("create_scopes")
    lab("seed_printer")
    grid = lab.grid.grid
    assert grid.find("fixedaddress", ipv4addr=cfg.PRINTER_IP)["mac"] == cfg.PRINTER_OLD_MAC
    assert changed(lab("seed_printer")) == 0

    lab("replace_reservation", "-e", f"ip={cfg.PRINTER_IP}", "-e", "mac=02-00-00-01-F0-02")
    fixed = grid.find("fixedaddress", ipv4addr=cfg.PRINTER_IP)
    assert fixed["mac"] == cfg.PRINTER_NEW_MAC and fixed["name"] == cfg.PRINTER_NAME
    assert len(grid.of_type("fixedaddress")) == 1             # replaced, not duplicated

    logs = lab.repo / "logs"
    assert (logs / "reservation-changes.txt").read_text() == f"{cfg.PRINTER_IP}\n{cfg.PRINTER_NEW_MAC}\n"
    audit = (logs / "reservation-audit.log").read_text()
    assert f"old_mac={cfg.PRINTER_OLD_MAC} new_mac={cfg.PRINTER_NEW_MAC}" in audit and "branch=BR-1001" in audit

    again = lab("replace_reservation", "-e", f"ip={cfg.PRINTER_IP}", "-e", f"mac={cfg.PRINTER_NEW_MAC}")
    assert changed(again) == 0                                # same request: nothing to do, nothing logged
    assert (logs / "reservation-changes.txt").read_text().count(cfg.PRINTER_IP) == 1

    lab("replace_reservation", "-e", f"ip={cfg.PRINTER_IP}", "-e", "mac=02:00:00:01:f0:03")
    assert (logs / "reservation-changes.txt").read_text().splitlines() == [
        cfg.PRINTER_IP, cfg.PRINTER_NEW_MAC, cfg.PRINTER_IP, "02:00:00:01:f0:03"]   # appended, as son.txt is


def test_replace_reservation_restores_the_old_one_when_the_add_fails(lab):
    lab("create_scopes")
    lab("seed_printer")
    lab.grid.grid.reject_macs.add("02:00:00:01:f0:09")
    result = lab("replace_reservation", "-e", f"ip={cfg.PRINTER_IP}", "-e", "mac=02:00:00:01:f0:09", check=False)
    assert result.returncode != 0
    assert "The old reservation was restored" in result.stdout
    assert lab.grid.grid.find("fixedaddress", ipv4addr=cfg.PRINTER_IP)["mac"] == cfg.PRINTER_OLD_MAC


@pytest.mark.parametrize("extra,message", [
    (["-e", "ip=10.100.11.60", "-e", "mac=nonsense"], "Usage:"),
    (["-e", "ip=10.100.11.60"], "Usage:"),
    (["-e", "ip=192.168.99.9", "-e", "mac=02:00:00:01:f0:09"], "not inside any network"),
])
def test_replace_reservation_rejects_bad_requests(lab, extra, message):
    lab("create_scopes")
    result = lab("replace_reservation", *extra, check=False)
    assert result.returncode != 0 and message in result.stdout


def test_push_preview_shows_what_each_server_would_receive(lab):
    """push_macs.yml --tags preview runs everything that decides the content, and sends nothing.

    The wake-up servers are pointed at local connections here (there is no WinRM in a test), so
    this checks the part most likely to break: which server gets which scopes' MACs.
    """
    lab("create_scopes")
    seed_leases(lab.grid)
    (lab.repo / "ansible" / "inventory" / "lab.yml").write_text(
        "---\nall:\n  children:\n    wakeup:\n      hosts:\n"
        "        wakeup1: {ansible_connection: local, wakeup_private_ip: 10.100.11.111}\n"
        "        wakeup2: {ansible_connection: local, wakeup_private_ip: 10.100.12.111}\n")
    result = lab("push_macs", "--tags", "preview")
    assert "wakeup1 <- 12 MAC addresses, from 10.100.11.0/24" in result.stdout
    assert "wakeup2 <- 24 MAC addresses, from 10.100.12.0/24, 10.100.13.0/24" in result.stdout
    assert not any(k in result.stdout for k in ("Write mac.txt", "win_template"))


def test_push_preview_reports_a_scope_whose_server_is_not_in_the_inventory(lab):
    lab("create_scopes")
    seed_leases(lab.grid)
    (lab.repo / "ansible" / "inventory" / "lab.yml").write_text(
        "---\nall:\n  children:\n    wakeup:\n      hosts:\n"
        "        wakeup1: {ansible_connection: local, wakeup_private_ip: 10.100.11.111}\n")
    result = lab("push_macs", "--tags", "preview")
    assert "10.100.12.111" in result.stdout and "is not in the inventory" in result.stdout
    assert "wakeup1 <- 12 MAC addresses" in result.stdout


def test_create_scopes_recovers_networks_left_without_a_member(lab):
    """A first run that stopped at the ranges leaves networks with no member; the next run must fix them."""
    grid = lab.grid.grid
    for net in ("10.100.11", "10.100.12", "10.100.13"):
        grid.add("network", {"network": f"{net}.0/24", "network_view": "default", "members": []})
    lab("create_scopes")
    assert all(n["members"] == [{"_struct": "dhcpmember", "name": cfg.MEMBERS[0]["name"]}]
               for n in grid.of_type("network"))
    assert len(grid.of_type("range")) == 3
