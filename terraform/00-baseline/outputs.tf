output "gm_public_ip" {
  description = "Grid Manager and WAPI address."
  value       = aws_eip.gm.public_ip
}

output "gm_lan1_ip" {
  description = "Grid Master LAN1 (VIP). Members join the grid here, over the VPC."
  value       = local.gm_lan1_ip
}

output "grid_subnet_id" {
  description = "Consumed by 01-members."
  value       = aws_subnet.grid.id
}

output "lab_sg_id" {
  description = "Consumed by 01-members."
  value       = aws_security_group.lab.id
}

output "key_name" {
  description = "Consumed by 01-members."
  value       = aws_key_pair.lab.key_name
}

output "ssh_key_path" {
  description = "Private key for the branch simulator."
  value       = abspath(local_sensitive_file.lab_key.filename)
}

output "wakeup_servers" {
  description = "Wake-up servers: the address the extensible attribute (or the .111 convention) names, and the public address Ansible connects to."
  value = {
    for name, server in local.wakeup_servers : name => {
      branch     = server.branch
      private_ip = local.branches[server.branch].wakeup_ip
      public_ip  = aws_eip.wakeup[name].public_ip
    }
  }
}

output "sim_public_ip" {
  description = "SSH address of the branch simulator."
  value       = aws_eip.sim.public_ip
}

output "branch_relays" {
  description = "Per branch: subnet and the simulator's address in it (the DHCP relay address)."
  value = {
    for name, branch in local.branches : name => {
      cidr     = branch.cidr
      relay_ip = branch.sim_ip
    }
  }
}
