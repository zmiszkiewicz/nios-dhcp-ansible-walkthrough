"""The lab's Python scripts against the fake grid."""
import json
from pathlib import Path

import pytest

import join_members
import lab_config as cfg
import lease_plan
import make_inventory
import verify_lab
import wait_for_grid
from nios_wapi import flatten_ea, normalize_version


def test_wapi_version_normalization():
    assert normalize_version("v2.12.3") == "2.12.3"
    assert normalize_version(None) == "2.12.3"


def test_wait_for_grid_succeeds_when_wapi_answers(grid, monkeypatch):
    monkeypatch.setattr("sys.argv", ["wait_for_grid.py", "--timeout", "10", "--interval", "0", "--stable", "2"])
    assert wait_for_grid.main() == 0


def test_wait_for_grid_fails_with_wrong_password(grid, monkeypatch):
    monkeypatch.setenv("INFOBLOX_PASSWORD", "wrong")
    monkeypatch.setattr("sys.argv", ["wait_for_grid.py", "--timeout", "1", "--interval", "0", "--stable", "1"])
    assert wait_for_grid.main() == 1


# --- joining the members -----------------------------------------------------

def test_prepare_creates_members_tokens_and_cloud_init(api, grid, tmp_path):
    out = tmp_path / "members.auto.tfvars.json"
    assert join_members.prepare(api, out) == 0

    for member in cfg.MEMBERS:
        obj = grid.grid.find("member", host_name=member["name"])
        assert obj["platform"] == "VNIOS"
        assert obj["vip_setting"] == {"address": member["ip"], "subnet_mask": cfg.GRID_NETMASK,
                                      "gateway": cfg.GRID_GATEWAY}
        assert obj["pre_provisioning"]["hardware_info"] == [{"hwtype": cfg.MEMBER_HWTYPE}]
        assert obj["pre_provisioning"]["licenses"] == cfg.MEMBER_LICENSES

    user_data = json.loads(out.read_text())["member_cloud_init"]
    assert set(user_data) == {m["key"] for m in cfg.MEMBERS}
    first = user_data["member1"]
    assert first.startswith("#infoblox-config\n")
    assert f"temp_license: {cfg.MEMBER_TEMP_LICENSE}" in first
    assert f"  ip_addr: {cfg.GM_LAN1_IP}" in first            # joins over the private VIP
    assert "  token: token-" in first
    assert "    -----BEGIN CERTIFICATE-----" in first
    parsed = __import__("yaml").safe_load(first)
    assert parsed["lan1"]["v4_addr"] == cfg.MEMBERS[0]["ip"]            # LAN1 serves DHCP
    assert parsed["mgmt"]["v4_addr"] == cfg.MEMBERS[0]["mgmt_ip"]       # and MGMT is configured too
    assert 'default_admin_password: "test"' in first
    assert oct(out.stat().st_mode & 0o777) == "0o600"          # it holds join tokens


def test_prepare_is_repeatable(api, tmp_path):
    out = tmp_path / "m.json"
    join_members.prepare(api, out)
    join_members.prepare(api, out)
    names = [m["host_name"] for m in api.get("member")]
    assert sorted(names) == sorted(["infoblox.localdomain"] + [m["name"] for m in cfg.MEMBERS])


def test_cloud_init_escapes_a_difficult_password():
    text = join_members.cloud_init(cfg.MEMBERS[0], "tok", "-----BEGIN CERTIFICATE-----\nAAA\n-----END CERTIFICATE-----",
                                   'pa"ss\\word$')
    import yaml
    parsed = yaml.safe_load(text)
    assert parsed["default_admin_password"] == 'pa"ss\\word$'
    assert parsed["gridmaster"]["certificate"].startswith("-----BEGIN CERTIFICATE-----")


def test_wait_enables_dhcp_once_members_have_joined(api, grid, tmp_path):
    join_members.prepare(api, tmp_path / "m.json")
    for member in cfg.MEMBERS:
        grid.grid.join_member(member["name"])
    assert join_members.wait(api, timeout=5, interval=0) == 0
    for member in cfg.MEMBERS:
        props = next(p for p in grid.grid.of_type("member:dhcpproperties") if p["host_name"] == member["name"])
        assert props["enable_dhcp"] is True


def test_wait_fails_clearly_when_a_member_never_joins(api, tmp_path):
    join_members.prepare(api, tmp_path / "m.json")
    assert join_members.wait(api, timeout=0.2, interval=0.05) == 1


# --- the lease plan ----------------------------------------------------------

def make_scopes(api):
    for index, net in enumerate(("10.100.11", "10.100.12", "10.100.13")):
        member = cfg.MEMBERS[0]
        api.post("network", {"network": f"{net}.0/24", "extattrs": {"Branch": {"value": f"BR-100{index + 1}"}},
                             "members": [{"_struct": "dhcpmember", "name": member["name"]}]})
        api.post("range", {"network": f"{net}.0/24", "start_addr": f"{net}.100", "end_addr": f"{net}.200",
                           "server_association_type": "MEMBER",
                           "member": {"_struct": "dhcpmember", "name": member["name"]}})


def test_lease_plan_reads_scope_to_member_from_the_grid(api, grid):
    for member in cfg.MEMBERS:
        grid.grid.add("member", {"host_name": member["name"], "vip_setting": {"address": member["ip"]}})
    make_scopes(api)
    plan = lease_plan.build_plan(api, count=7)
    assert [p["network"] for p in plan] == ["10.100.11.0/24", "10.100.12.0/24", "10.100.13.0/24"]
    assert [p["scope_index"] for p in plan] == [1, 2, 3]
    assert [p["relay_ip"] for p in plan] == ["10.100.11.20", "10.100.12.20", "10.100.13.20"]
    assert [p["server_ip"] for p in plan] == ["10.100.0.21"] * 3      # one member serves every branch
    assert plan[0]["hostname_prefix"] == "br1001-pc" and plan[0]["count"] == 7


# --- verify_lab --------------------------------------------------------------

def test_verify_stages_pass_and_fail_for_the_right_reasons(api, grid):
    with pytest.raises(verify_lab.Check, match="extensible attribute"):
        verify_lab.stage_scopes(api)

    for name in verify_lab.EXPECTED_EAS:
        api.post("extensibleattributedef", {"name": name, "type": "STRING"})
    with pytest.raises(verify_lab.Check, match="does not exist"):
        verify_lab.stage_scopes(api)

    scopes = verify_lab.load_vars("scopes.yml")["scopes"]
    for scope in scopes:
        api.post("network", {"network": scope["network"],
                             "extattrs": {k: {"value": v} for k, v in scope["extattrs"].items()},
                             "members": [{"_struct": "dhcpmember", "name": verify_lab.member_name(scope["member"])}]})
        api.post("range", {"network": scope["network"], "start_addr": scope["range_start"],
                           "end_addr": scope["range_end"], "server_association_type": "MEMBER",
                           "member": {"_struct": "dhcpmember", "name": verify_lab.member_name(scope["member"])}})
    verify_lab.stage_scopes(api)  # passes

    with pytest.raises(verify_lab.Check, match="active leases"):
        verify_lab.stage_leases(api, minimum=3)
    for scope in scopes:
        net = scope["network"].split("/")[0].rsplit(".", 1)[0]
        for n in range(1, 4):
            grid.grid.add_lease(scope["network"], f"{net}.{100 + n}", cfg.mac_for(1, n))
        grid.grid.add_lease(scope["network"], f"{net}.250", "02:de:ad:00:00:01", state="EXPIRED")
    verify_lab.stage_leases(api, minimum=3)  # passes; the expired lease does not count


def test_expected_wakeup_files_follow_the_ea_then_the_convention(api, grid):
    api.post("network", {"network": "10.100.11.0/24", "extattrs": {"Branch": {"value": "BR-1"}}})
    api.post("network", {"network": "10.100.12.0/24", "extattrs": {
        "Branch": {"value": "BR-2"}, "WakeUp_Server": {"value": "10.100.12.111"}}})
    api.post("network", {"network": "10.100.13.0/24", "extattrs": {
        "Branch": {"value": "BR-3"}, "WakeUp_Server": {"value": "10.100.12.111"}}})
    api.post("network", {"network": "10.200.0.0/24"})  # not a branch scope: ignored
    for net, last, mac in [("10.100.11", 100, "02:00:00:01:00:01"), ("10.100.12", 101, "02:00:00:02:00:01"),
                           ("10.100.13", 102, "02:00:00:03:00:01"), ("10.100.12", 99, "02:00:00:02:00:02")]:
        grid.grid.add_lease(f"{net}.0/24", f"{net}.{last}", mac)

    files = verify_lab.expected_wakeup_files(api)
    assert set(files) == {"10.100.11.111", "10.100.12.111"}          # branch 1 by convention, 2 and 3 share
    assert files["10.100.11.111"] == b"\r\n\r\n02:00:00:01:00:01\r\n"
    # the shared server: scope 2 in IP order (.99 before .101), then scope 3
    assert files["10.100.12.111"] == (b"\r\n\r\n02:00:00:02:00:02\r\n02:00:00:02:00:01\r\n"
                                      b"02:00:00:03:00:01\r\n")


def test_reservation_stage(api, grid, tmp_path, monkeypatch):
    api.post("fixedaddress", {"name": "p", "ipv4addr": cfg.PRINTER_IP, "mac": cfg.PRINTER_OLD_MAC})
    with pytest.raises(verify_lab.Check, match="still reserved"):
        verify_lab.stage_reservation(api)
    fixed = grid.grid.find("fixedaddress", ipv4addr=cfg.PRINTER_IP)
    fixed["mac"] = cfg.PRINTER_NEW_MAC
    monkeypatch.setattr(verify_lab, "REPO_ROOT", tmp_path)
    with pytest.raises(verify_lab.Check, match="not logged"):
        verify_lab.stage_reservation(api)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "reservation-changes.txt").write_text(f"{cfg.PRINTER_IP}\n{cfg.PRINTER_NEW_MAC}\n")
    verify_lab.stage_reservation(api)  # passes


# --- inventory ---------------------------------------------------------------

def test_inventory_generation(tmp_path, monkeypatch):
    outputs = {
        "gm_public_ip": {"value": "3.3.3.3"}, "sim_public_ip": {"value": "4.4.4.4"},
        "ssh_key_path": {"value": "/x/key.pem"},
        "branch_relays": {"value": {"br1": {"cidr": "10.100.11.0/24", "relay_ip": "10.100.11.20"}}},
        "wakeup_servers": {"value": {
            "wakeup1": {"branch": "br1", "private_ip": "10.100.11.111", "public_ip": "1.1.1.1"},
            "wakeup2": {"branch": "br2", "private_ip": "10.100.12.111", "public_ip": "2.2.2.2"}}},
    }
    saved = tmp_path / "outputs.json"
    saved.write_text(json.dumps(outputs))
    target = tmp_path / "lab.yml"
    monkeypatch.setattr(make_inventory, "INVENTORY", target)
    monkeypatch.setattr(make_inventory, "REPO_ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["make_inventory.py", "--from-json", str(saved)])
    monkeypatch.setenv("LAB_STATE_DIR", str(tmp_path / "state"))
    assert make_inventory.main() == 0

    import yaml
    inventory = yaml.safe_load(target.read_text())
    hosts = inventory["all"]["children"]["wakeup"]["hosts"]
    assert hosts["wakeup2"] == {"ansible_host": "2.2.2.2", "wakeup_private_ip": "10.100.12.111", "branch": "br2"}
    variables = inventory["all"]["children"]["wakeup"]["vars"]
    assert variables["ansible_port"] == 5986 and variables["ansible_winrm_scheme"] == "https"
    assert "password" not in target.read_text().lower().replace("ansible_password", "").replace("windows_admin_password", "")
    assert json.loads((tmp_path / "state" / "lab.json").read_text())["sim_public_ip"] == "4.4.4.4"


def test_flatten_ea():
    assert flatten_ea({"Branch": {"value": "BR-1"}, "Site": "x"}) == {"Branch": "BR-1", "Site": "x"}


def test_file_downloads_send_the_content_type_nios_requires(api):
    """A download without Content-Type: application/force-download gets HTTP 415 from NIOS."""
    from nios_wapi import WapiError
    import requests
    url = f"https://{api.host}/http_direct_file_io/x/cert.pem"
    assert requests.get(url, auth=("admin", "test"), verify=False).status_code == 415
    assert "BEGIN CERTIFICATE" in api.raw_get(url)
    with pytest.raises(WapiError):
        api.session.auth = ("admin", "wrong")
        api.raw_get(url)
