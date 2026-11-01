"""The Ansible filter plugin (ansible/filter_plugins/lab.py)."""
import pytest


def test_sort_by_ip_is_numeric(lab_filters):
    leases = [{"address": a} for a in ("10.0.0.100", "10.0.0.20", "10.0.0.3")]
    assert [x["address"] for x in lab_filters.sort_by_ip(leases)] == ["10.0.0.3", "10.0.0.20", "10.0.0.100"]


def test_sort_by_ip_accepts_cidr(lab_filters):
    nets = [{"network": "10.100.12.0/24"}, {"network": "10.100.2.0/24"}]
    assert [n["network"] for n in lab_filters.sort_by_ip(nets, "network")] == ["10.100.2.0/24", "10.100.12.0/24"]


def test_containing_network_picks_most_specific(lab_filters):
    nets = [{"network": "10.100.0.0/16"}, {"network": "10.100.11.0/24"}, {"network": "10.100.12.0/24"}]
    assert lab_filters.containing_network(nets, "10.100.11.60")["network"] == "10.100.11.0/24"
    assert lab_filters.containing_network(nets, "10.100.99.1")["network"] == "10.100.0.0/16"
    assert lab_filters.containing_network(nets, "192.168.1.1") is None


@pytest.mark.parametrize("raw", ["02:00:00:01:F0:02", "02-00-00-01-f0-02", "02-00-00-01-F0-02"])
def test_normalize_mac_accepts_both_forms(lab_filters, raw):
    assert lab_filters.normalize_mac(raw) == "02:00:00:01:f0:02"


@pytest.mark.parametrize("raw", ["nonsense", "02:00:00:01:f0", "02:00:00:01:f0:0g", "02:00-00:01:f0:02"])
def test_normalize_mac_rejects_garbage(lab_filters, raw):
    from ansible.errors import AnsibleFilterError
    with pytest.raises(AnsibleFilterError):
        lab_filters.normalize_mac(raw)


def test_ms_clientid_is_dashed_lowercase(lab_filters):
    assert lab_filters.ms_clientid("AA:BB:CC:00:11:22") == "aa-bb-cc-00-11-22"


def test_convention_wakeup_matches_the_original(lab_filters):
    # $ip.Substring(0, $ip.LastIndexOf(".")) + ".111"
    assert lab_filters.convention_wakeup("10.100.11.0/24") == "10.100.11.111"
    assert lab_filters.convention_wakeup("10.100.11.0") == "10.100.11.111"


def test_group_macs_merges_shared_servers_and_reports_unmapped(lab_filters):
    plan = [
        {"network": "a", "wakeup_host": "w1", "macs": ["m1", "m2"]},
        {"network": "b", "wakeup_host": "w2", "macs": ["m3", "m4"]},
        {"network": "c", "wakeup_host": "w2", "macs": ["m4", "m5"]},   # shares w2, one MAC repeated
        {"network": "d", "wakeup_host": "", "macs": ["m6"]},           # no server in the inventory
    ]
    grouped = lab_filters.group_macs(plan)
    assert grouped["hosts"]["w1"] == {"macs": ["m1", "m2"], "scopes": ["a"]}
    assert grouped["hosts"]["w2"] == {"macs": ["m3", "m4", "m5"], "scopes": ["b", "c"]}
    assert [item["network"] for item in grouped["unmapped"]] == ["d"]


def test_table_aligns_columns(lab_filters):
    lines = lab_filters.table([{"a": "x", "b": "long value"}, {"a": "yy", "b": "z"}],
                              [{"title": "A", "key": "a"}, {"title": "B", "key": "b"}])
    assert lines[0] == "A   B"
    assert lines[1] == "--  ----------"
    assert lines[2] == "x   long value"
    assert lines[3] == "yy  z"
