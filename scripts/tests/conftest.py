"""Shared fixtures. Run from the repo root:  python3 -m pytest scripts/tests -q"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent
REPO = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TESTS))

from fake_wapi import FakeServer  # noqa: E402


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def lab_filters():
    """The Ansible filter plugin, loaded by path (it is not on sys.path)."""
    return load_module("lab_filters", REPO / "ansible" / "filter_plugins" / "lab.py")


@pytest.fixture
def grid(monkeypatch, tmp_path):
    """A fake grid on HTTPS, with the environment the scripts and modules read."""
    with FakeServer(password="test") as server:
        monkeypatch.setenv("INFOBLOX_HOST", server.host)
        monkeypatch.setenv("INFOBLOX_PASSWORD", "test")
        monkeypatch.setenv("INFOBLOX_USERNAME", "admin")
        monkeypatch.setenv("WAPI_RETRY_SECONDS", "0")
        monkeypatch.setenv("LAB_STATE_DIR", str(tmp_path / "state"))
        yield server


@pytest.fixture
def api(grid):
    from nios_wapi import NiosWapi
    client = NiosWapi.from_env()
    client.detect_version()
    return client
