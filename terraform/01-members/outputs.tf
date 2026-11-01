output "member_private_ips" {
  description = "Member LAN1 addresses (the DHCP servers the relay sends to)."
  value       = { for name, nic in aws_network_interface.member : name => one(nic.private_ips) }
}
