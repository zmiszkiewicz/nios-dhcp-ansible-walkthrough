#!/bin/bash
#
# Get the branch simulator ready: wait for SSH, copy lease_gen.py onto it, make
# sure its source-based routing is in place, and check it owns an address in every
# branch subnet. Run by challenge 1's setup; safe to run again.
#
# Needs SIM_IP (its public address) and LAB_SSH_KEY (the private key Terraform wrote).
set -euo pipefail

: "${SIM_IP:?SIM_IP is not set (the baseline Terraform output sim_public_ip)}"
: "${LAB_SSH_KEY:?LAB_SSH_KEY is not set (the baseline Terraform output ssh_key_path)}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${HERE}"   # the inline Python below imports its sibling modules
SIM_DIR="$(python3 lab_config.py get sim_dir)"
SSH_OPTS=(-i "${LAB_SSH_KEY}" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
          -o LogLevel=ERROR -o ConnectTimeout=10 -o BatchMode=yes)

echo "=== Waiting for the branch simulator at ${SIM_IP} ==="
ready=0
for attempt in $(seq 1 60); do
  # The routing helper is installed by the VM's first-boot script, so its presence
  # means first boot has finished, not merely that sshd is up.
  if ssh "${SSH_OPTS[@]}" "ubuntu@${SIM_IP}" "test -x /usr/local/sbin/lab-policy-routing" 2>/dev/null; then
    ready=1
    break
  fi
  echo "  ... not ready (attempt ${attempt}/60)"
  sleep 10
done
if [[ "${ready}" -ne 1 ]]; then
  echo "FATAL: the branch simulator did not become reachable over SSH within 10 minutes."
  echo "       Check the instance in the AWS console and that port 22 is open."
  exit 1
fi

scp "${SSH_OPTS[@]}" "${HERE}/lease_gen.py" "ubuntu@${SIM_IP}:${SIM_DIR}/lease_gen.py"
ssh "${SSH_OPTS[@]}" "ubuntu@${SIM_IP}" "sudo /usr/local/sbin/lab-policy-routing"

addresses="$(ssh "${SSH_OPTS[@]}" "ubuntu@${SIM_IP}" "ip -4 -o addr show")"

# The relay address of every branch, from the lab state. Failing to read it must fail the
# script: an empty list would make the check below pass without checking anything.
relays="$(python3 -c '
from nios_wapi import load_state
for branch in load_state("lab").get("branch_relays", {}).values():
    print(branch["relay_ip"])
')"
if [[ -z "${relays}" ]]; then
  echo "FATAL: no branch relay addresses in the lab state. Run: python3 make_inventory.py"
  exit 1
fi

missing=0
while read -r relay; do
  if grep -q "inet ${relay}/" <<<"${addresses}"; then
    echo "  [OK] simulator owns ${relay}"
  else
    echo "  [FAIL] simulator does not have ${relay}"
    missing=1
  fi
done <<<"${relays}"
exit "${missing}"
