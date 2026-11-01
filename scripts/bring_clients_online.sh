#!/bin/bash
#
# Bring the branch DHCP clients online so leases appear in NIOS.
#
# For every DHCP range on the grid it logs in to the branch simulator and runs
# lease_gen.py, which sends DHCP requests from that branch's relay address to the
# member serving the range. The scope-to-member assignment is read from NIOS
# (lease_plan.py), so this works for whatever the playbook created.
#
#   scripts/bring_clients_online.sh            # CLIENTS_PER_SCOPE clients each (default 30)
#   CLIENTS_PER_SCOPE=50 scripts/bring_clients_online.sh
#
# Running it again renews the same leases (the client MACs are deterministic).
set -euo pipefail

: "${SIM_IP:?SIM_IP is not set (the baseline Terraform output sim_public_ip)}"
: "${LAB_SSH_KEY:?LAB_SSH_KEY is not set (the baseline Terraform output ssh_key_path)}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${HERE}"
SIM_DIR="$(python3 lab_config.py get sim_dir)"
SSH_OPTS=(-i "${LAB_SSH_KEY}" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
          -o LogLevel=ERROR -o ConnectTimeout=10 -o BatchMode=yes)

plan="$(python3 lease_plan.py)"
work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

echo "=== Bringing the branch clients online ==="
pids=()
while IFS=$'\t' read -r index network relay server count prefix; do
  echo "  ${network}: ${count} clients, relay ${relay}, DHCP server ${server}"
  # -n: ssh must not read the rest of the plan from stdin. The command is built here on
  # purpose; the values are ours (read from NIOS), not user input.
  # shellcheck disable=SC2029
  ssh -n "${SSH_OPTS[@]}" "ubuntu@${SIM_IP}" \
    "sudo python3 ${SIM_DIR}/lease_gen.py --server ${server} --giaddr ${relay} --count ${count} --scope-index ${index} --hostname-prefix ${prefix}" \
    > "${work}/${index}.out" 2> "${work}/${index}.err" &
  pids+=("$!")
done <<<"${plan}"

failed=0
for pid in "${pids[@]}"; do
  wait "${pid}" || failed=1
done

for out in "${work}"/*.out; do
  index="$(basename "${out}" .out)"
  summary="$(tail -n 1 "${out}")"
  if jq -e '.summary' <<<"${summary}" >/dev/null 2>&1; then
    jq -r '"  [" + (if .leased == .requested then "OK" else "FAIL" end) + "] relay \(.giaddr): \(.leased) of \(.requested) clients got a lease"' <<<"${summary}"
  else
    echo "  [FAIL] scope ${index}: no result"
    cat "${work}/${index}.err" || true
    failed=1
  fi
done

if [[ "${failed}" -ne 0 ]]; then
  echo
  echo "Some clients did not get a lease. Likely causes: the member's DHCP service is not running yet,"
  echo "the range is not assigned to a member, or UDP 67 is blocked between the simulator and the member."
  echo "Details: run lease_gen.py by hand on the simulator (see the README)."
  exit 2
fi
