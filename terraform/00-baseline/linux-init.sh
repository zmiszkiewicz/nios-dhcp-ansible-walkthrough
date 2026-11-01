#!/bin/bash
# First boot of the branch simulator.
#
# The VM has three network interfaces, one per branch subnet. AWS drops a packet
# whose source address does not belong to the interface it leaves on, and Linux
# has one default route, so without help a request sent from the branch 2 address
# would try to leave through the branch 1 interface and vanish. This script gives
# each interface its own routing table and a rule that selects it by source
# address, and brings up any interface the image did not configure itself.
#
# Idempotent: it runs once now and again on every boot (systemd unit below).
set -u

install -d -m 0755 -o ubuntu -g ubuntu /opt/lab

cat > /usr/local/sbin/lab-policy-routing <<'SCRIPT'
#!/bin/bash
set -u
TOKEN=$(curl -s -X PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 120")
imds() { curl -s -H "X-aws-ec2-metadata-token: $TOKEN" "http://169.254.169.254/latest/meta-data/$1"; }

table=100
for mac in $(imds network/interfaces/macs/); do
  mac=${mac%/}
  addr=$(imds "network/interfaces/macs/$mac/local-ipv4s" | head -1)
  cidr=$(imds "network/interfaces/macs/$mac/subnet-ipv4-cidr-block")
  [ -z "$addr" ] && continue
  ifname=$(grep -il "$mac" /sys/class/net/*/address | head -1 | awk -F/ '{print $5}')
  [ -z "$ifname" ] && continue

  prefix=${cidr#*/}
  gateway=$(python3 -c 'import ipaddress,sys; print(ipaddress.ip_network(sys.argv[1]).network_address + 1)' "$cidr")

  ip link set "$ifname" up
  ip -4 addr show dev "$ifname" | grep -q "inet $addr/" || ip addr add "$addr/$prefix" dev "$ifname"

  table=$((table + 1))
  ip route replace "$cidr" dev "$ifname" src "$addr" table "$table"
  ip route replace default via "$gateway" dev "$ifname" table "$table"
  ip rule show | grep -q "from $addr lookup $table" || ip rule add from "$addr/32" table "$table"
done
SCRIPT
chmod 0755 /usr/local/sbin/lab-policy-routing

cat > /etc/systemd/system/lab-policy-routing.service <<'UNIT'
[Unit]
Description=Source-based routing for the branch simulator interfaces
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/lab-policy-routing
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable lab-policy-routing.service
/usr/local/sbin/lab-policy-routing
