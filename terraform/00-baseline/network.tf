# One VPC, one grid subnet and three branch subnets.
#
# The branch subnets are real VPC subnets on purpose. NIOS picks a DHCP scope from
# the relay address (giaddr) in a request, and the reply goes back to that address,
# so the lease generator must own an address in every branch subnet. A VPC only
# routes addresses that belong to one of its subnets.

locals {
  gm_mgmt_ip = "10.100.0.10"
  gm_lan1_ip = "10.100.0.11"

  # Branch subnets. sim_ip is the lease generator's address in the branch (it is
  # the relay address, giaddr). wakeup_ip is where a branch's wake-up server lives
  # when it has one: the original script's <first three octets>.111 convention.
  branches = {
    br1 = { cidr = "10.100.11.0/24", sim_ip = "10.100.11.20", wakeup_ip = "10.100.11.111", index = 0 }
    br2 = { cidr = "10.100.12.0/24", sim_ip = "10.100.12.20", wakeup_ip = "10.100.12.111", index = 1 }
    br3 = { cidr = "10.100.13.0/24", sim_ip = "10.100.13.20", wakeup_ip = null, index = 2 }
  }

  # Two wake-up servers (the vCPU budget does not stretch to a third).
  wakeup_servers = {
    wakeup1 = { branch = "br1" }
    wakeup2 = { branch = "br2" }
  }
}

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${var.name_prefix}-vpc" }
}

# Every subnet sits in the same zone: an instance can only attach network
# interfaces from its own zone, and the branch simulator uses three.
resource "aws_subnet" "grid" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.100.0.0/24"
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = false
  tags                    = { Name = "${var.name_prefix}-grid" }
}

resource "aws_subnet" "branch" {
  for_each                = local.branches
  vpc_id                  = aws_vpc.main.id
  cidr_block              = each.value.cidr
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = false
  tags                    = { Name = "${var.name_prefix}-${each.key}" }
}

resource "aws_internet_gateway" "gw" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.name_prefix}-igw" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.gw.id
  }

  tags = { Name = "${var.name_prefix}-rt" }
}

resource "aws_route_table_association" "grid" {
  subnet_id      = aws_subnet.grid.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "branch" {
  for_each       = local.branches
  subnet_id      = aws_subnet.branch[each.key].id
  route_table_id = aws_route_table.public.id
}

# One security group for the whole lab. Everything inside the VPC is open (this is
# where DHCP relay, the grid VPN and the Ansible-driven traffic all flow); from the
# internet only the ports the lab uses.
resource "aws_security_group" "lab" {
  name        = "${var.name_prefix}-lab"
  description = "Lab: grid UI/API, SSH, RDP and WinRM over HTTPS from the internet, everything inside the VPC"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "Grid Manager and WAPI"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    description = "SSH to the branch simulator"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    description = "RDP to the wake-up servers (through the lab's browser desktop)"
    from_port   = 3389
    to_port     = 3389
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    description = "WinRM over HTTPS, which is how Ansible reaches the wake-up servers"
    from_port   = 5986
    to_port     = 5986
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    description = "Everything inside the VPC"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.name_prefix}-lab" }
}

# SSH key for the branch simulator. Written next to the Terraform, mode 0400.
resource "tls_private_key" "lab" {
  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "aws_key_pair" "lab" {
  key_name   = "${var.name_prefix}-key"
  public_key = tls_private_key.lab.public_key_openssh
}

resource "local_sensitive_file" "lab_key" {
  filename        = "${path.module}/lab-key.pem"
  content         = tls_private_key.lab.private_key_pem
  file_permission = "0400"
}
