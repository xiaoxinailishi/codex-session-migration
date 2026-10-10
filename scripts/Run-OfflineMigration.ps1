param(
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$CodexHome,
    [string]$SQLiteHome,
    [string]$Prepared,
    [string]$Backup,
    [string]$ToolRoot = (Split-Path -Parent $PSScriptRoot)
)
$ErrorActionPreference = 'Stop'
$taskExitCode = 0
try {
    if ([bool]$Prepared -eq [bool]$Backup) { throw 'Choose exactly one: -Prepared (apply) or -Backup (rollback).' }
    Set-Location -LiteralPath $ToolRoot
    Write-Host 'Run this script in PowerShell opened from Start or Win+R.'
    Write-Host 'Completely quit Codex, including its tray and app-server.'
    $taskAcknowledgement = Read-Host 'Then press Enter; enter CANCEL to stop'
    if ($taskAcknowledgement -eq 'CANCEL') { return }
    $taskCommand = if ($Prepared) { 'apply' } else { 'rollback' }
    $taskArguments = @('-m','codex_migrate',$taskCommand,'--home',$CodexHome,'--confirm')
    if ($SQLiteHome) { $taskArguments += @('--sqlite-home',$SQLiteHome) }
    if ($Prepared) { $taskArguments += @('--prepared',$Prepared) }
    else { $taskArguments += @('--backup',$Backup) }
    & $Python @taskArguments
    if ($LASTEXITCODE -ne 0) { throw "Migration stopped (exit $LASTEXITCODE). Read the error before retrying." }
    if ($Prepared) {
        $taskValidation = Get-Content -LiteralPath (Join-Path $Prepared 'validation.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        & $Python -m codex_migrate verify --stage $taskValidation.stage
        if ($LASTEXITCODE -ne 0) { throw 'Data verification did not pass. Keep backups and inspect the report.' }
        Write-Host 'Data verification passed. Start Codex and inspect chats, project assignments and images.'
    }
} catch {
    $taskExitCode = 1
    Write-Error -Message $_ -ErrorAction Continue
} finally {
    Read-Host 'Press Enter to close this script' | Out-Null
}
exit $taskExitCode
