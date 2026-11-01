#!/usr/bin/env python3
"""Run PowerShell on a wake-up server over WinRM (HTTPS), for the checks and resets.

The playbooks use Ansible's Windows modules for the real work; this exists so the
check scripts can read a file back from a server without starting Ansible.

  python3 winrm_helper.py <host> '<powershell>'

Credentials: WINDOWS_ADMIN_USER (default Administrator) and WINDOWS_ADMIN_PASSWORD.
"""
from __future__ import annotations

import os
import sys

import lab_config as cfg


class WinRMError(RuntimeError):
    pass


def run_ps(host, script, port=5986, timeout=60):
    """Return (exit_code, stdout, stderr). Raises WinRMError if the host cannot be reached."""
    import winrm  # imported here so the rest of the lab scripts do not need pywinrm

    password = os.environ.get("WINDOWS_ADMIN_PASSWORD")
    if not password:
        raise WinRMError("WINDOWS_ADMIN_PASSWORD is not set")
    session = winrm.Session(
        f"https://{host}:{port}/wsman",
        auth=(cfg.WAKEUP_USER, password),
        transport="basic",
        server_cert_validation="ignore",
        operation_timeout_sec=timeout,
        read_timeout_sec=timeout + 10,
    )
    try:
        result = session.run_ps(script)
    except Exception as exc:  # pywinrm raises many exception types; report one way
        raise WinRMError(f"{host}: {exc.__class__.__name__}: {exc}") from exc
    return (result.status_code,
            result.std_out.decode("utf-8", errors="replace"),
            result.std_err.decode("utf-8", errors="replace"))


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 1
    code, out, err = run_ps(argv[0], argv[1])
    sys.stdout.write(out)
    sys.stderr.write(err)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
