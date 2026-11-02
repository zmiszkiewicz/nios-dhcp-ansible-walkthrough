#!/bin/bash
#
# The branch printer asks DHCP for an address, as a device with a given MAC.
#
#   scripts/printer_dhcp.sh new      the replacement printer (PRINTER_NEW_MAC)
#   scripts/printer_dhcp.sh old      the failed printer (PRINTER_OLD_MAC)
#   scripts/printer_dhcp.sh <MAC>    any MAC
#
# Before the reservation swap the new printer gets an ordinary address from the
# pool; after it, the printer's reserved address. Same DHCP exchange both times.
set -euo pipefail

# Use the lab's virtualenv (ansible-core, requests, ...) whatever PATH this was started with.
[[ -x /opt/lab-venv/bin/python3 ]] && PATH="/opt/lab-venv/bin:${PATH}"

: "${SIM_IP:?SIM_IP is not set (the baseline Terraform output sim_public_ip)}"
: "${LAB_SSH_KEY:?LAB_SSH_KEY is not set (the baseline Terraform output ssh_key_path)}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${HERE}"
SIM_DIR="$(python3 lab_config.py get sim_dir)"
SSH_OPTS=(-i "${LAB_SSH_KEY}" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
          -o LogLevel=ERROR -o ConnectTimeout=10 -o BatchMode=yes)

case "${1:-new}" in
  new) mac="$(python3 lab_config.py get printer_new_mac)" ;;
  old) mac="$(python3 lab_config.py get printer_old_mac)" ;;
  *)   mac="$1" ;;
esac
name="$(python3 lab_config.py get printer_name)"

# The printer sits behind the relay of the scope that contains its address.
read -r relay server < <(python3 - <<'PY'
import ipaddress
import lab_config as cfg
from lease_plan import build_plan
from nios_wapi import NiosWapi

api = NiosWapi.from_env()
api.detect_version()
for item in build_plan(api):
    if ipaddress.ip_address(cfg.PRINTER_IP) in ipaddress.ip_network(item["network"]):
        print(item["relay_ip"], item["server_ip"])
        break
PY
)
if [[ -z "${relay:-}" ]]; then
  echo "ERROR: no DHCP range found for the printer's address. Run the create_scopes playbook first."
  exit 1
fi

echo "=== Printer ${name} (${mac}) asks for an address via ${relay}, server ${server} ==="
# The command is built here on purpose (SC2029); the values come from NIOS and the lab config.
# shellcheck disable=SC2029
result="$(ssh "${SSH_OPTS[@]}" "ubuntu@${SIM_IP}" \
  "sudo python3 ${SIM_DIR}/lease_gen.py --server ${server} --giaddr ${relay} --mac ${mac} --hostname ${name}" \
  | head -n 1)"
echo "${result}" | jq -r 'if .status == "ACK" then "  got \(.ip) (lease \(.lease_seconds) seconds)" else "  no lease: \(.status)" end'
[[ "$(jq -r '.status' <<<"${result}")" == "ACK" ]]
