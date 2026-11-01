# Branch wake-up servers: Windows Server 2022 with WinRM over HTTPS.
#
# Each one lives at <branch subnet>.111, the address the original PowerShell
# derives from a scope. Ansible reaches them through their public address (the
# control node is outside the VPC), so each also gets an Elastic IP.

data "aws_ami" "windows" {
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["Windows_Server-2022-English-Full-Base-*"]
  }
}

resource "aws_network_interface" "wakeup" {
  for_each        = local.wakeup_servers
  subnet_id       = aws_subnet.branch[each.value.branch].id
  private_ips     = [local.branches[each.value.branch].wakeup_ip]
  security_groups = [aws_security_group.lab.id]
  tags            = { Name = "${var.name_prefix}-${each.key}" }
}

resource "aws_instance" "wakeup" {
  for_each      = local.wakeup_servers
  ami           = data.aws_ami.windows.id
  instance_type = var.windows_instance_type
  key_name      = aws_key_pair.lab.key_name

  network_interface {
    network_interface_id = aws_network_interface.wakeup[each.key].id
    device_index         = 0
  }

  # The password travels base64-encoded so a quote or dollar sign in it cannot
  # break the PowerShell.
  user_data = templatefile("${path.module}/windows-init.ps1.tpl", {
    admin_password_b64 = base64encode(var.windows_admin_password)
  })

  tags = { Name = "${var.name_prefix}-${each.key}" }

  depends_on = [aws_internet_gateway.gw]
}

resource "aws_eip" "wakeup" {
  for_each = local.wakeup_servers
  domain   = "vpc"
  tags     = { Name = "${var.name_prefix}-${each.key}-eip" }
}

resource "aws_eip_association" "wakeup" {
  for_each             = local.wakeup_servers
  network_interface_id = aws_network_interface.wakeup[each.key].id
  allocation_id        = aws_eip.wakeup[each.key].id
  private_ip_address   = local.branches[each.value.branch].wakeup_ip

  depends_on = [aws_instance.wakeup]
}
