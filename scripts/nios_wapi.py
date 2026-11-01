#!/usr/bin/env python3
"""Small NIOS WAPI client shared by the lab scripts.

Credentials come from the environment, using the names the Ansible
infoblox.nios_modules collection reads, so one set of variables serves both:

  INFOBLOX_HOST          Grid Master address, optionally host:port
                         (falls back to INFOBLOX_SERVER, then GM_IP)
  INFOBLOX_USERNAME      defaults to admin
  INFOBLOX_PASSWORD      NIOS admin password
  INFOBLOX_WAPI_VERSION  defaults to 2.12.3 (falls back to WAPI_VERSION;
                         a leading "v" is optional)
  WAPI_SCHEME            https (default); only a test server would use http
  WAPI_RETRY_SECONDS     how long a call keeps retrying when the grid drops the
                         connection (default 240). A booting vNIOS restarts its
                         web service after it first answers, so this is routine.

2.12.3 is the WAPI version the collection defaults to and the Terraform provider
is built against, so every call here is valid on older grids and accepted by
newer ones.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DEFAULT_WAPI = "2.12.3"
FALLBACK_VERSIONS = ["2.12", "2.13.1", "2.13", "2.14"]
RETRY_STATUSES = (502, 503, 504)


class WapiError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def normalize_version(version):
    """Return a bare version string: 'v2.12.3' and '2.12.3' both give '2.12.3'."""
    version = (version or DEFAULT_WAPI).strip()
    return version[1:] if version.lower().startswith("v") else version


def flatten_ea(extattrs):
    """WAPI returns {"Site": {"value": "x"}}; return {"Site": "x"}."""
    return {k: v.get("value") if isinstance(v, dict) else v for k, v in (extattrs or {}).items()}


def state_dir():
    """Run-time state (addresses, inventory facts). Never committed, see .gitignore."""
    path = Path(os.getenv("LAB_STATE_DIR") or Path(__file__).resolve().parent.parent / ".state")
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_state(name, data):
    (state_dir() / f"{name}.json").write_text(json.dumps(data, indent=2))


def load_state(name):
    path = state_dir() / f"{name}.json"
    return json.loads(path.read_text()) if path.exists() else {}


class NiosWapi:
    def __init__(self, host, username="admin", password="", version=None, timeout=30):
        self.host = host
        self.scheme = os.getenv("WAPI_SCHEME", "https")
        self.version = normalize_version(version)
        self.timeout = timeout
        self.retry_seconds = float(os.getenv("WAPI_RETRY_SECONDS", "240"))
        self.session = requests.Session()
        self.session.auth = (username, password)
        self.session.verify = False

    @classmethod
    def from_env(cls):
        host = os.getenv("INFOBLOX_HOST") or os.getenv("INFOBLOX_SERVER") or os.getenv("GM_IP")
        password = os.getenv("INFOBLOX_PASSWORD")
        if not host:
            raise WapiError("INFOBLOX_HOST (or GM_IP) is not set")
        if not password:
            raise WapiError("INFOBLOX_PASSWORD is not set")
        return cls(
            host,
            os.getenv("INFOBLOX_USERNAME", "admin"),
            password,
            os.getenv("INFOBLOX_WAPI_VERSION") or os.getenv("WAPI_VERSION"),
        )

    @property
    def base(self):
        return f"{self.scheme}://{self.host}/wapi/v{self.version}"

    def _request(self, method, path, params=None, payload=None):
        url = path if path.startswith("http") else f"{self.base}/{path}"
        deadline = time.time() + self.retry_seconds
        delay = 2
        while True:
            try:
                resp = self.session.request(
                    method, url, params=params, json=payload, timeout=self.timeout
                )
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                # Connection reset or refused: the web service is restarting.
                if time.time() + delay > deadline:
                    raise WapiError(f"{method} {url}: {exc.__class__.__name__}: {exc}") from exc
                print(f"  (grid not answering, retrying in {delay}s: {exc.__class__.__name__})")
                time.sleep(delay)
                delay = min(delay * 2, 15)
                continue
            except requests.exceptions.RequestException as exc:
                raise WapiError(f"{method} {url}: {exc.__class__.__name__}: {exc}") from exc
            if resp.status_code in RETRY_STATUSES and time.time() + delay <= deadline:
                print(f"  (grid returned HTTP {resp.status_code}, retrying in {delay}s)")
                time.sleep(delay)
                delay = min(delay * 2, 15)
                continue
            break
        if not 200 <= resp.status_code < 300:
            raise WapiError(
                f"{method} {path}: HTTP {resp.status_code}: {resp.text[:300]}",
                status=resp.status_code,
            )
        return resp.json() if resp.content else None

    def raw_get(self, url):
        """GET an absolute URL (a WAPI file download) with the same credentials."""
        resp = self.session.get(url, timeout=self.timeout)
        if resp.status_code != 200:
            raise WapiError(f"GET {url}: HTTP {resp.status_code}: {resp.text[:200]}", status=resp.status_code)
        return resp.text

    def detect_version(self):
        """Use the preferred WAPI version, falling back to others the grid accepts."""
        candidates = [self.version] + [v for v in FALLBACK_VERSIONS if v != self.version]
        last = None
        for candidate in candidates:
            url = f"{self.scheme}://{self.host}/wapi/v{candidate}/grid"
            try:
                resp = self.session.get(url, timeout=self.timeout)
            except requests.exceptions.RequestException as exc:
                raise WapiError(f"cannot reach {url}: {exc.__class__.__name__}") from exc
            if resp.status_code == 200:
                self.version = candidate
                return candidate
            if resp.status_code in (401, 403):
                raise WapiError(f"authentication failed (HTTP {resp.status_code})", status=resp.status_code)
            last = resp.status_code
        raise WapiError(f"no supported WAPI version answered (last HTTP {last})", status=last)

    def get(self, obj, params=None, fields=None, max_results=5000):
        """GET an object type or a _ref. `fields` are added to the default return fields."""
        params = dict(params or {})
        if fields:
            params["_return_fields+"] = ",".join(fields)
        if max_results and "_max_results" not in params:
            params["_max_results"] = max_results
        result = self._request("GET", obj, params=params)
        found = result if isinstance(result, list) else [result]
        return [item for item in found if item]

    def first(self, obj, params=None, fields=None):
        found = self.get(obj, params=params, fields=fields, max_results=1)
        return found[0] if found else None

    def post(self, obj, payload, params=None):
        return self._request("POST", obj, params=params, payload=payload)

    def put(self, ref, payload):
        return self._request("PUT", ref, payload=payload)

    def delete(self, ref):
        return self._request("DELETE", ref)

    def call(self, ref, function, payload=None):
        """Invoke a WAPI function on an object, e.g. create_token."""
        return self._request("POST", ref, params={"_function": function}, payload=payload or {})


def log(message, ok=True):
    print(f"  [{'OK' if ok else 'FAIL'}] {message}")


def wait_until(check, timeout, interval, describe):
    """Poll `check()` until it returns truthy or the timeout expires."""
    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        result = check()
        if result:
            return result
        remaining = int(deadline - time.time())
        print(f"  ... waiting for {describe} (attempt {attempt}, {remaining}s left)")
        time.sleep(interval)
    return None


if __name__ == "__main__":
    api = NiosWapi.from_env()
    print(f"WAPI version in use: v{api.detect_version()}")
    print(json.dumps(api.get("grid"), indent=2))
    sys.exit(0)
