param(
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$Stage,
    [Parameter(Mandatory=$true)][string]$BindAddress,
    [Parameter(Mandatory=$true)][string]$PeerAddress,
    [Parameter(Mandatory=$true)][string]$Connection,
    [ValidateRange(1,65535)][int]$Port = 49473,
    [ValidateRange(1,120)][int]$Minutes = 30,
    [switch]$OpenFirewall,
    [string]$ToolRoot = (Split-Path -Parent $PSScriptRoot)
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $ToolRoot
$taskRuleName = 'CodexSessionMigration-' + [Guid]::NewGuid().ToString('N')
$taskRuleCreated = $false
try {
    if ($OpenFirewall) {
        $taskPythonPath = (Get-Command $Python -ErrorAction Stop).Source
        if (-not $taskPythonPath) { throw 'Use the full path of an installed Python executable.' }
        New-NetFirewallRule -Name $taskRuleName -DisplayName $taskRuleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -LocalAddress $BindAddress -RemoteAddress $PeerAddress -Program $taskPythonPath -Profile Any | Out-Null
        $taskRuleCreated = $true
    }
    Write-Host 'Trusted LAN only. This HTTP transport is not encrypted.'
    Write-Host 'Privately share the connection file only with the intended receiving computer.'
    & $Python -m codex_migrate serve --stage $Stage --bind $BindAddress --peer $PeerAddress --port $Port --minutes $Minutes --connection $Connection
    if ($LASTEXITCODE -ne 0) { throw "Transfer server stopped with exit $LASTEXITCODE." }
} finally {
    if ($taskRuleCreated) { Remove-NetFirewallRule -Name $taskRuleName -ErrorAction Continue }
    Write-Host 'Server ended. Confirm the listener is closed and the temporary firewall rule is gone.'
}
