#!/bin/bash
#
# Skip ahead: bring the lab to the end state of a challenge.
#
#   scripts/solve.sh 2     scopes created, clients online
#   scripts/solve.sh 3     ... and the MACs collected
#   scripts/solve.sh 4     ... and pushed to the wake-up servers, comparison run
#   scripts/solve.sh 5     ... and the printer's reservation swapped
#
# Each stage includes the earlier ones. Everything is idempotent, so running it
# when part of the work is already done is fine.
set -euo pipefail

# Use the lab's virtualenv (ansible-core, requests, ...) whatever PATH this was started with.
[[ -x /opt/lab-venv/bin/python3 ]] && PATH="/opt/lab-venv/bin:${PATH}"

stage="${1:-}"
if [[ ! "${stage}" =~ ^[2-5]$ ]]; then
  echo "Usage: $0 2|3|4|5"
  exit 1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"

run() {
  echo
  echo "=== $* ==="
  "$@"
}

cd "${ROOT}/ansible"

run ansible-playbook playbooks/create_scopes.yml
run "${HERE}/bring_clients_online.sh"
[[ "${stage}" -ge 3 ]] && run ansible-playbook playbooks/collect_macs.yml
if [[ "${stage}" -ge 4 ]]; then
  run ansible-playbook playbooks/push_macs.yml
  run ansible-playbook playbooks/compare_legacy.yml
fi
if [[ "${stage}" -ge 5 ]]; then
  # Put the printer back on its original MAC first (this also clears the change log), so the
  # swap below is a real swap even when it has been done before.
  run python3 "${HERE}/reset_lab.py" 5
  run ansible-playbook playbooks/replace_reservation.yml -e "@${ROOT}/solutions/05-reservation/replace_reservation.vars.yml"
  run "${HERE}/printer_dhcp.sh" new
fi
exit 0
