#!/bin/bash
#
# Run this about ten minutes before a session. It checks the control node has
# what the playbooks need and that every part of the lab answers. It changes
# nothing. The exit status is the number of checks that failed.
#
#   scripts/preflight.sh
set -uo pipefail

# Use the lab's virtualenv (ansible-core, requests, ...) whatever PATH this was started with.
[[ -x /opt/lab-venv/bin/python3 ]] && PATH="/opt/lab-venv/bin:${PATH}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${HERE}" || exit 1
failures=0

ok()   { echo "  [OK]   $*"; }
fail() { echo "  [FAIL] $*"; failures=$((failures + 1)); }

echo "=== Tools ==="
for tool in ansible-playbook ansible-galaxy terraform python3 jq ssh; do
  if command -v "${tool}" >/dev/null 2>&1; then ok "${tool}"; else fail "${tool} is not installed"; fi
done

echo "=== Python packages ==="
for module in infoblox_client winrm yaml requests boto3; do
  if python3 -c "import ${module}" 2>/dev/null; then ok "${module}"; else fail "python module ${module} is missing (pip install -r requirements.txt)"; fi
done

echo "=== Ansible collections ==="
collections="$(ansible-galaxy collection list 2>/dev/null || true)"
for name in infoblox.nios_modules ansible.windows; do
  if grep -q "${name}" <<<"${collections}"; then ok "${name}"; else fail "${name} is missing (ansible-galaxy collection install -r ansible/requirements.yml)"; fi
done

echo "=== Environment ==="
for var in INFOBLOX_HOST INFOBLOX_PASSWORD WINDOWS_ADMIN_PASSWORD SIM_IP LAB_SSH_KEY; do
  if [[ -n "${!var:-}" ]]; then ok "${var}"; else fail "${var} is not set"; fi
done

echo "=== Grid, wake-up servers and simulator ==="
if python3 verify_lab.py --stage baseline; then ok "baseline"; else fail "baseline (see the reason above)"; fi

echo
if [[ "${failures}" -eq 0 ]]; then
  echo "Everything is ready."
else
  echo "${failures} check(s) failed."
fi
exit "${failures}"
