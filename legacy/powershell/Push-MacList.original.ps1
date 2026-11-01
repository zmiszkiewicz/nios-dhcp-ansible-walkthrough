# ORIGINAL wake-up script (sanitized copy, for side-by-side reading; not run by the lab).
#
# Runs on each Microsoft DHCP server, every business day. For every IPv4 scope it
# exports the MAC address of each lease and copies the list to that branch's
# wake-up server, found by convention as <first three octets of the scope>.111.
#
# Sanitized: the wake-up server variable was renamed. The logic is unchanged.
#
# The Ansible equivalent is ansible/playbooks/push_macs.yml; the mapping, step by
# step, is in the lab guide.

$sco=(Get-DhcpServerv4Scope).ScopeID
foreach ($scope in $sco) {
  $ip = $scope.IPAddressToString
  $sep = ($ip).LastIndexOf(".")
  $network = $ip.Substring(0,$sep)
  $wakeupServer = $network + ".111"
  (Remove-Item -Path \\$wakeupServer\c$\windows\system32\mac.txt -Force)
  (Remove-Item -Path \\$wakeupServer\c$\windows\system32\$scope.txt -Force)
  Get-DHCPServerv4Lease -ScopeID $scope | Select-Object ClientID | Export-Csv c:\windows\system32\$scope.txt
  (Get-Content c:\windows\system32\$scope.txt) | Foreach-Object {$_ -replace '#TYPE Selected.Microsoft.Management.Infrastructure.CimInstance' -replace 'ClientId' -replace '"','' -replace '-',':'} | Set-Content c:\windows\system32\$scope.txt
  robocopy c:\windows\system32\ \\$wakeupServer\c$\windows\system32\ "$scope.txt" /w:0 /r:0
  ren \\$wakeupServer\c$\windows\system32\$scope.txt "mac.txt"
}
