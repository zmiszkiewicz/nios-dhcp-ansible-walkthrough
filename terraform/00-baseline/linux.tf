# The branch simulator: one small Linux VM with an interface in each branch subnet.
#
# It plays two roles. It is the relay agent and client population for the three
# branches (each branch's DHCP requests leave from that branch's own address, so
# NIOS sees a request from that subnet), and it is the branch printer. It is not
# the Ansible control node; that is the Instruqt shell.

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }
}

resource "aws_network_interface" "sim" {
  for_each        = local.branches
  subnet_id       = aws_subnet.branch[each.key].id
  private_ips     = [each.value.sim_ip]
  security_groups = [aws_security_group.lab.id]
  tags            = { Name = "${var.name_prefix}-sim-${each.key}" }
}

resource "aws_instance" "sim" {
  ami           = data.aws_ami.ubuntu.id
  instance_type = var.sim_instance_type
  key_name      = aws_key_pair.lab.key_name

  # br1 is the primary interface (device 0) and carries the public address.
  dynamic "network_interface" {
    for_each = local.branches
    content {
      network_interface_id = aws_network_interface.sim[network_interface.key].id
      device_index         = network_interface.value.index
    }
  }

  user_data = file("${path.module}/linux-init.sh")

  tags = { Name = "${var.name_prefix}-branch-sim" }

  depends_on = [aws_internet_gateway.gw]
}

resource "aws_eip" "sim" {
  domain = "vpc"
  tags   = { Name = "${var.name_prefix}-sim-eip" }
}

resource "aws_eip_association" "sim" {
  network_interface_id = aws_network_interface.sim["br1"].id
  allocation_id        = aws_eip.sim.id
  private_ip_address   = local.branches["br1"].sim_ip

  depends_on = [aws_instance.sim]
}
