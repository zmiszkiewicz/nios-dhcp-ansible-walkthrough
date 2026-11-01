"""scripts/prepare_simulator.sh with stand-in ssh and scp, started from an unrelated folder.

A live boot found that the script (started from the repo root) could not import its sibling
Python module, and that it then reported success without having checked anything.
"""
import json
import os
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "prepare_simulator.sh"
RELAYS = {"br1": {"cidr": "10.100.11.0/24", "relay_ip": "10.100.11.20"},
          "br2": {"cidr": "10.100.12.0/24", "relay_ip": "10.100.12.20"}}


def stub(directory, name, body):
    path = directory / name
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def run(tmp_path, owned, state=True):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    lines = "".join(f"2: ens5    inet {ip}/24 brd 10.100.1.255 scope global ens5\\n" for ip in owned)
    # ssh answers the readiness probe, the routing helper and the address listing; scp does nothing
    stub(bin_dir, "ssh", f'case "$*" in *"ip -4 -o addr show"*) printf "{lines}";; esac\nexit 0\n')
    stub(bin_dir, "scp", "exit 0\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    if state:
        (state_dir / "lab.json").write_text(json.dumps({"branch_relays": RELAYS}))
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", SIM_IP="203.0.113.9",
               LAB_SSH_KEY="/nonexistent", LAB_STATE_DIR=str(state_dir))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    return subprocess.run(["bash", str(SCRIPT)], cwd=elsewhere, env=env, capture_output=True, text=True)


def test_passes_when_the_simulator_owns_every_branch_address(tmp_path):
    result = run(tmp_path, ["10.100.11.20", "10.100.12.20"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "simulator owns 10.100.11.20" in result.stdout and "simulator owns 10.100.12.20" in result.stdout


def test_fails_when_an_address_is_missing(tmp_path):
    result = run(tmp_path, ["10.100.11.20"])
    assert result.returncode == 1
    assert "does not have 10.100.12.20" in result.stdout


def test_fails_instead_of_passing_silently_when_the_lab_state_is_missing(tmp_path):
    result = run(tmp_path, ["10.100.11.20", "10.100.12.20"], state=False)
    assert result.returncode == 1
    assert "no branch relay addresses" in result.stdout
