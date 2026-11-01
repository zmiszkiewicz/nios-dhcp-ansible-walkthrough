variable "aws_region" {
  description = "AWS region. The vNIOS AMI below is only known to exist in eu-central-1."
  type        = string
  default     = "eu-central-1"
}

variable "name_prefix" {
  description = "Prefix for resource names and tags."
  type        = string
  default     = "dhcp-lab"
}

variable "vpc_cidr" {
  type    = string
  default = "10.100.0.0/16"
}

variable "gm_ami_id" {
  description = "vNIOS Grid Master AMI (the same image the other Infoblox labs use)."
  type        = string
  default     = "ami-0f223da0ec214a840"
}

variable "gm_instance_type" {
  description = "Grid Master size. 4 vCPU, matching the other labs that run this AMI."
  type        = string
  default     = "m5.xlarge"
}

variable "windows_instance_type" {
  description = "Wake-up server size (2 vCPU)."
  type        = string
  default     = "t3.medium"
}

variable "sim_instance_type" {
  description = "Branch simulator size. Must allow three network interfaces (t3.small does; t3.nano does not)."
  type        = string
  default     = "t3.small"
}

variable "nios_admin_password" {
  description = "NIOS admin password. Supply it as TF_VAR_nios_admin_password, never in a file."
  type        = string
  sensitive   = true
}

variable "windows_admin_password" {
  description = "Windows Administrator password. Supply it as TF_VAR_windows_admin_password, never in a file."
  type        = string
  sensitive   = true
}
