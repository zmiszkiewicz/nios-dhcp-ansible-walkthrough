# ORIGINAL reservation-swap script (sanitized copy, for side-by-side reading; not run by the lab).
#
# When a branch device with a reserved IP (a printer, say) is replaced, the branch
# desk emails the operations mailbox with a keyword subject, and this runs with the
# device's IP and the new device's MAC: it logs the request, removes the old
# reservation and adds a new one on the same IP.
#
# Sanitized: variable names, the server name and the log path were replaced. The
# logic is unchanged. $ipAddress, $macAddress and $subnet are filled in by the
# surrounding automation that reads the email.
#
# The Ansible equivalent is ansible/playbooks/replace_reservation.yml.

$ipAddress,$macAddress | out-file -filepath C:\logs\son.txt -append -width 200
Remove-DhcpServerv4Reservation -ComputerName 'dhcp-server' -IPAddress $ipAddress
Add-DhcpServerv4Reservation -ComputerName 'dhcp-server' -ScopeId $subnet -IPAddress $ipAddress -ClientId $macAddress -Name reservation
