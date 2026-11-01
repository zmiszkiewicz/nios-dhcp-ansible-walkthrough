<powershell>
$ErrorActionPreference = "Stop"
Start-Transcript -Path "C:\user_data.log" -Append

Write-Output "---- Wake-up server first boot: administrator password and WinRM over HTTPS ----"

# Administrator password (supplied base64-encoded by Terraform).
$plain  = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('${admin_password_b64}'))
$secure = New-Object System.Security.SecureString
foreach ($char in $plain.ToCharArray()) { $secure.AppendChar($char) }
Set-LocalUser -Name "Administrator" -Password $secure
Set-LocalUser -Name "Administrator" -PasswordNeverExpires $true

# Start WinRM, then replace any HTTPS listener with one bound to a new self-signed
# certificate. Ansible connects with certificate validation turned off.
winrm quickconfig -quiet -force
Set-Item -Path WSMan:\localhost\Service\Auth\Basic -Value $true

$cert = New-SelfSignedCertificate -DnsName $env:COMPUTERNAME -CertStoreLocation Cert:\LocalMachine\My
Get-ChildItem WSMan:\localhost\Listener |
    Where-Object { $_.Keys -contains "Transport=HTTPS" } |
    Remove-Item -Recurse -Force
New-Item -Path WSMan:\localhost\Listener -Address * -Transport HTTPS -CertificateThumbPrint $cert.Thumbprint -Force | Out-Null

foreach ($rule in @(@{ Name = "WinRM HTTPS"; Port = 5986 }, @{ Name = "RDP"; Port = 3389 })) {
    if (-not (Get-NetFirewallRule -DisplayName $rule.Name -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName $rule.Name -Direction Inbound -Action Allow -Protocol TCP -LocalPort $rule.Port | Out-Null
    }
}

Restart-Service WinRM -Force
Write-Output "---- Finished user_data ----"
Stop-Transcript
</powershell>
