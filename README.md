# DHCP automation with Infoblox NIOS and Red Hat Ansible

Companion code for the `nios-dhcp-ansible-walkthrough` Instruqt track. It re-creates two PowerShell workflows that
run against Microsoft DHCP as Ansible playbooks against an Infoblox NIOS grid, and builds the lab environment they run in.
All names, addresses and data are fictional.

| Workflow | Original (Microsoft DHCP) | Here |
|---|---|---|
| **A. Branch wake-up** | each DHCP server exports the MACs of every scope's leases and copies them to the branch's wake-up server as `C:\Windows\System32\mac.txt` | `push_macs.yml` |
| **B. Reservation swap** | a failed device's reservation is removed and re-added with the replacement's MAC, and the change logged | `replace_reservation.yml` |

The track is the guided walkthrough. This repo is also usable on its own: the playbooks run against any NIOS grid
(see [Run the playbooks against your own grid](#run-the-playbooks-against-your-own-grid)).

## Layout

```
ansible/
  ansible.cfg                run from this folder
  requirements.yml           collections, pinned
  inventory/
    localhost.yml            "NIOS" is the control node talking to the WAPI
    lab.yml                  wake-up servers (WinRM); generated in the lab, not committed
    group_vars/all/main.yml  connection settings (from environment variables) and options
    group_vars/all/scopes.yml  the data: scopes, extensible attributes ("API slots"), members, printer
  playbooks/
    create_scopes.yml        networks + DHCP ranges + extensible attributes, assigned to members
    collect_macs.yml         active leases per scope, MACs extracted and summarised
    push_macs.yml            write mac.txt on each wake-up server (--tags preview to only show)
    compare_legacy.yml       run the original -replace step on Windows and diff it with Ansible's output
    replace_reservation.yml  IP + new MAC in, reservation swapped, change logged
    seed_printer.yml         the reservation the swap starts from
  tasks/                     collect_scopes.yml (shared), export_scopes.yml
  templates/                 mac.txt.j2, ms-lease-export.csv.j2, reservation-*.j2
  filter_plugins/lab.py      a few small filters (sort by IP, MAC normalizing, grouping, tables)
  examples/                  wake-up server inventory example
legacy/
  powershell/                the two original scripts (sanitized) and the runner for their clean-up step
  samples/                   an Export-Csv sample and the file the original pipeline makes from it
terraform/
  00-baseline/               VPC, Grid Master, two Windows wake-up servers, branch simulator
  01-members/                the grid member (needs join tokens from a running Grid Master)
scripts/                     lab helpers: WAPI client, member join, lease generator, checks, reset, tests
solutions/                   the inputs challenge 5 uses, for the track's Solve buttons
requirements.txt             Python packages, pinned
```

## How the Instruqt track uses it

The track's setup script clones this repo (`main` by default; set `LAB_REPO_REF` to a tag), installs
`requirements.txt` into a virtualenv and `ansible/requirements.yml` as collections, and applies `terraform/00-baseline`.
Challenge 1's setup then:

1. waits for the Grid Master (`scripts/wait_for_grid.py`, polling, not a fixed sleep);
2. creates the member on the grid and writes its cloud-init (`scripts/join_members.py prepare`);
3. applies `terraform/01-members`, then waits for them to join and switches their DHCP service on (`join_members.py wait`);
4. writes the Ansible inventory from the Terraform outputs (`scripts/make_inventory.py`);
5. prepares the branch simulator (`scripts/prepare_simulator.sh`) and waits for WinRM on both Windows servers.

Each later challenge's `check-shell` calls `scripts/verify_lab.py --stage <name>`; its `solve-shell` calls `scripts/solve.sh <n>`.

Credentials arrive as environment variables from Instruqt secrets. Nothing secret is stored in this repo.

## Run the playbooks against your own grid

You need a control node with Python 3.11 or newer, network access to the Grid Master's WAPI (HTTPS), and, for the
Windows workflow, WinRM access to the wake-up servers.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cd ansible && ansible-galaxy collection install -r requirements.yml

export INFOBLOX_HOST=gm.example.net            # Grid Master address (host or host:port)
export INFOBLOX_USERNAME=admin
export INFOBLOX_PASSWORD='...'
export INFOBLOX_WAPI_VERSION=2.12.3            # optional; 2.12.3 is the default
export WINDOWS_ADMIN_PASSWORD='...'            # only for push_macs / compare_legacy
```

1. **Describe your scopes** in `ansible/inventory/group_vars/all/scopes.yml` (networks, ranges, member host names, slot values),
   or skip `create_scopes.yml` if the networks already exist. `collect_macs.yml` and `push_macs.yml` act on every network
   that carries a `Branch` extensible attribute.
2. **Describe your wake-up servers** in `ansible/inventory/lab.yml`, following `ansible/examples/wakeup-inventory.example.yml`. Each host needs
   `ansible_host` (where the control node connects) and `wakeup_private_ip` (the address NIOS knows it by: the `WakeUp_Server`
   attribute, or `<first three octets of the scope>.111`).
3. Run, from the `ansible` folder:

```bash
ansible-playbook playbooks/create_scopes.yml
ansible-playbook playbooks/collect_macs.yml
ansible-playbook playbooks/push_macs.yml --tags preview    # what each server would receive
ansible-playbook playbooks/push_macs.yml
ansible-playbook playbooks/replace_reservation.yml -e ip=10.100.11.60 -e mac=02:00:00:01:f0:02
```

You do not need the branch simulator or the Terraform: against a real grid the leases come from real clients.

### Options worth knowing

| Setting | Where | Default | What it does |
|---|---|---|---|
| `mac_legacy_blank_lines` | `group_vars/all/main.yml` | `2` | The original script blanks, rather than deletes, the `#TYPE` and `ClientId` lines, so its file starts with two empty lines. `2` reproduces that byte for byte; `0` writes a clean file. |
| `wakeup_file` | `group_vars/all/main.yml` | `C:\Windows\System32\mac.txt` | Where the wake-up software reads its list. |
| `forks` | `ansible.cfg` | `10` | How many wake-up servers are written at once. Raise it for a large estate. |
| `LAB_COMPANY` | environment | `ExampleBank` | Customer name shown in comments and logs. |

## Behaviour to know about

- **`nios_fixed_address` in `infoblox.nios_modules` 1.9.0 fails if `options` is left out** (its helper iterates `None`; found
  while testing this lab). Every call here passes `options: []`, which avoids it and changes nothing on the grid.
- **Only `ACTIVE` leases are collected**, filtered after the query: on WAPI 2.12.3 a lease search cannot filter on `binding_state`. A NIOS lease can be in other states too (the `binding_state` values include `STATIC`,
  `FREE`, `EXPIRED` and `OFFERED`). Microsoft's `Get-DhcpServerv4Lease` has its own rules for reservations; if the wake-up
  lists must include reserved devices, check how your grid reports them and extend the `binding_state` filter in
  `ansible/tasks/collect_scopes.yml`.
- **Two scopes can share a wake-up server.** The server receives the merged list. The original script, run for two scopes that map to the same server,
  would keep only the last one.
- **WinRM here is HTTPS with Basic authentication and certificate validation turned off.** That is acceptable for a throwaway lab
  with a self-signed certificate. In production use the transport your estate already uses for Windows automation (Kerberos or certificate authentication).
- **WAPI paging.** The lookups use the collection's default of 1,000 results per query. A /24 scope is well under that; for very large scopes raise
  `max_results` in `nios_provider` (`group_vars/all/main.yml`).

## Tests

```bash
pip install pytest        # on top of requirements.txt
python3 -m pytest scripts/tests -q
```

The tests run the lab's scripts and the real playbooks (with the real collection) against an in-process fake WAPI
(`scripts/tests/fake_wapi.py`) and a tiny DHCP server on loopback. They check that the code sends well-formed requests and computes
the right files, that the Terraform, Ansible and Python copies of shared facts agree (`test_consistency.py`), and, if PowerShell
is installed, that the original `-replace` pipeline produces what the template produces. They do not prove NIOS or Windows behave the
same way; see the track README for what has to be confirmed on a live boot.
