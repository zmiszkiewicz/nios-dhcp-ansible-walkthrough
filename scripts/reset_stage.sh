#!/bin/bash
#
# Put the lab back to the start of a challenge.
#
#   scripts/reset_stage.sh 2     remove the scopes (back to a bare grid)
#   scripts/reset_stage.sh 3     delete the exported files
#   scripts/reset_stage.sh 4     remove mac.txt from the wake-up servers
#   scripts/reset_stage.sh 5     restore the printer's original reservation
#   scripts/reset_stage.sh all   all of the above
#
# The grid, its members and the VMs are left alone, so this takes seconds.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${HERE}"
exec python3 reset_lab.py "$@"
