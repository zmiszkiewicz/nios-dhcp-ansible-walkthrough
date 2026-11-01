terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.20"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# The grid member(s). A separate stage because each needs a join token
# and the Grid Master's certificate, and only a running Grid Master can issue
# those. scripts/join_members.py prepare creates the member objects on the grid,
# collects the tokens and writes members.auto.tfvars.json, which this stage reads.
data "terraform_remote_state" "baseline" {
  backend = "local"
  config = {
    path = "${path.module}/../00-baseline/terraform.tfstate"
  }
}

locals {
  subnet_id = data.terraform_remote_state.baseline.outputs.grid_subnet_id
  sg_id     = data.terraform_remote_state.baseline.outputs.lab_sg_id
  key_name  = data.terraform_remote_state.baseline.outputs.key_name
}

# LAN1 only. A member with a single interface uses it as LAN1.
resource "aws_network_interface" "member" {
  for_each        = var.members
  subnet_id       = local.subnet_id
  private_ips     = [each.value.ip]
  security_groups = [local.sg_id]
  tags            = { Name = "${var.name_prefix}-${each.key}" }
}

resource "aws_instance" "member" {
  for_each      = var.members
  ami           = var.member_ami_id
  instance_type = var.member_instance_type
  key_name      = local.key_name

  network_interface {
    network_interface_id = aws_network_interface.member[each.key].id
    device_index         = 0
  }

  user_data = var.member_cloud_init[each.key]

  tags = { Name = "${var.name_prefix}-nios-${each.key}" }
}
