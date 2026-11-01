# NIOS Grid Master. Same image, license string and two-interface layout the other
# Infoblox labs use. The two grid members are a separate stage (01-members):
# a member can only be created once the Grid Master is up and has issued it a
# join token.

resource "aws_network_interface" "gm_mgmt" {
  subnet_id       = aws_subnet.grid.id
  private_ips     = [local.gm_mgmt_ip]
  security_groups = [aws_security_group.lab.id]
  tags            = { Name = "${var.name_prefix}-gm-mgmt" }
}

# Same zone as MGMT, as a multi-interface instance requires.
resource "aws_network_interface" "gm_lan1" {
  subnet_id       = aws_subnet.grid.id
  private_ips     = [local.gm_lan1_ip]
  security_groups = [aws_security_group.lab.id]
  tags            = { Name = "${var.name_prefix}-gm-lan1" }
}

resource "aws_instance" "gm" {
  ami           = var.gm_ami_id
  instance_type = var.gm_instance_type
  key_name      = aws_key_pair.lab.key_name

  network_interface {
    network_interface_id = aws_network_interface.gm_mgmt.id
    device_index         = 0
  }
  network_interface {
    network_interface_id = aws_network_interface.gm_lan1.id
    device_index         = 1
  }

  user_data = <<-EOF
#infoblox-config
temp_license: nios IB-V825 enterprise dns dhcp cloud
remote_console_enabled: y
default_admin_password: "${var.nios_admin_password}"
lan1:
  v4_addr: ${local.gm_lan1_ip}
  v4_netmask: 255.255.255.0
  v4_gw: 10.100.0.1
mgmt:
  v4_addr: ${local.gm_mgmt_ip}
  v4_netmask: 255.255.255.0
  v4_gw: 10.100.0.1
EOF

  tags = { Name = "${var.name_prefix}-nios-gm" }

  depends_on = [aws_internet_gateway.gw]
}

# Public address for Grid Manager and the WAPI, on LAN1.
resource "aws_eip" "gm" {
  domain = "vpc"
  tags   = { Name = "${var.name_prefix}-gm-eip" }
}

resource "aws_eip_association" "gm" {
  network_interface_id = aws_network_interface.gm_lan1.id
  allocation_id        = aws_eip.gm.id
  private_ip_address   = local.gm_lan1_ip

  depends_on = [aws_instance.gm]
}
