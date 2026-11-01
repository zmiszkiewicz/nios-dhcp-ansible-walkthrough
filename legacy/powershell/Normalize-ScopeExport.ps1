<#
.SYNOPSIS
    The normalization step of the original wake-up script, run on its own.

.DESCRIPTION
    The original script exports a scope's leases with Export-Csv and then cleans the
    file up with a -replace pipeline. A lab has no Microsoft DHCP server to export
    from, so the export step is replaced by a CSV in exactly the format it writes
    (see legacy/samples/leases-export.sample.csv). The pipeline below is the
    original, character for character; only the file paths differ.

.PARAMETER ScopeId
    The scope's network address, for example 10.100.11.0. Reads <Dir>\<ScopeId>.csv
    and writes <Dir>\<ScopeId>.txt.

.PARAMETER Dir
    Working folder. Defaults to C:\legacy.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$ScopeId,

    [string]$Dir = 'C:\legacy'
)

$scope = $ScopeId
$csv = Join-Path $Dir "$scope.csv"
$txt = Join-Path $Dir "$scope.txt"

(Get-Content $csv) | Foreach-Object {$_ -replace '#TYPE Selected.Microsoft.Management.Infrastructure.CimInstance' -replace 'ClientId' -replace '"','' -replace '-',':'} | Set-Content $txt
