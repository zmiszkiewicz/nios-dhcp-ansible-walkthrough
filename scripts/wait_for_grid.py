#!/usr/bin/env python3
"""Wait until the Grid Master answers WAPI with working credentials.

A vNIOS Grid Master needs several minutes after the instance starts to boot,
apply its license and bring up the WAPI. Poll instead of sleeping for a fixed
time, and fail with a message that names what is wrong.

  python3 wait_for_grid.py [--timeout 900] [--interval 15] [--stable 3]

The WAPI can answer once during boot and then restart its web service, so a
single success is not enough: it must answer --stable times in a row.
"""
from __future__ import annotations

import argparse
import sys

from nios_wapi import NiosWapi, WapiError, wait_until


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--interval", type=int, default=15)
    parser.add_argument("--stable", type=int, default=3,
                        help="consecutive successful checks required (default 3)")
    args = parser.parse_args()

    api = NiosWapi.from_env()
    api.retry_seconds = 0  # this loop is the retry; do not nest another inside it
    print(f"=== Waiting for the Grid Master at {api.host} (up to {args.timeout}s) ===")
    last_error = {"text": ""}
    streak = {"n": 0}

    def ready():
        try:
            api.detect_version()
            ok = bool(api.get("member", max_results=5))
        except WapiError as exc:
            last_error["text"] = str(exc)
            ok = False
        streak["n"] = streak["n"] + 1 if ok else 0
        if ok and streak["n"] < args.stable:
            print(f"  WAPI answered ({streak['n']}/{args.stable}); confirming it stays up")
        return streak["n"] >= args.stable

    if wait_until(ready, args.timeout, args.interval, "the Grid Master WAPI"):
        print(f"  [OK] WAPI v{api.version} is answering on {api.host}")
        return 0

    print(f"  [FAIL] the Grid Master did not become ready within {args.timeout}s")
    print(f"         last error: {last_error['text']}")
    print("         Check the instance in the AWS console and its system log, and that")
    print("         the admin password secret was set when the instance was created.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
