variable "aws_region" {
  type    = string
  default = "eu-central-1"
}

variable "name_prefix" {
  type    = string
  default = "dhcp-lab"
}

variable "member_ami_id" {
  description = "vNIOS AMI for the members (same image as the Grid Master)."
  type        = string
  default     = "ami-0f223da0ec214a840"
}

variable "member_instance_type" {
  description = "Member size (4 vCPU, the size the Grid Master runs at with the same hardware model)."
  type        = string
  default     = "m5.xlarge"
}

variable "members" {
  description = "Grid members and their LAN1 and MGMT addresses in the grid subnet. Keep in step with scripts/lab_config.py."
  type = map(object({
    ip      = string # LAN1
    mgmt_ip = string # MGMT
  }))
  default = {
    member1 = { ip = "10.100.0.21", mgmt_ip = "10.100.0.20" }
  }
}

variable "member_cloud_init" {
  description = "Per-member #infoblox-config user data, written by scripts/join_members.py prepare into members.auto.tfvars.json. It contains a join token."
  type        = map(string)
  sensitive   = true
}
