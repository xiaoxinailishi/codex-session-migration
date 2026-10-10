param(
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$Stage,
    [string]$ToolRoot = (Split-Path -Parent $PSScriptRoot)
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $ToolRoot
& $Python -m codex_migrate cleanup-plan --stage $Stage
if ($LASTEXITCODE -ne 0) { throw 'Cleanup planning stopped.' }
$taskPlanPath = Join-Path $Stage 'cleanup-plan.json'
$taskPlan = Get-Content -LiteralPath $taskPlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $taskPlan.data_verification_passed) { throw 'Finish data verification before deleting staging entries.' }
$taskGui = Read-Host 'Have you checked chats and actual images in Codex? Type YES to continue'
if ($taskGui -cne 'YES') { Write-Host 'Nothing deleted.'; return }
Write-Host 'Backups remain selected OFF by default. Live images are protected.'
$taskRawNames = Read-Host 'Enter exact top-level names separated by semicolons; Enter keeps everything'
if ([string]::IsNullOrWhiteSpace($taskRawNames)) { Write-Host 'Nothing deleted.'; return }
$taskNames = @($taskRawNames.Split(';') | ForEach-Object { $_.Trim() } | Where-Object { $_ } | Select-Object -Unique)
$taskArguments = @('-m','codex_migrate','cleanup','--plan',$taskPlanPath,'--confirm')
$taskBackupSelected = $false
foreach ($taskName in $taskNames) {
    $taskEntry = @($taskPlan.entries | Where-Object { $_.path -ceq $taskName })
    if ($taskEntry.Count -ne 1) { throw "Unknown entry: $taskName" }
    if ($taskEntry[0].role -eq 'rollback-backup') { $taskBackupSelected = $true }
    $taskArguments += @('--select',$taskName)
}
if ($taskBackupSelected) {
    $taskAllowBackup = Read-Host 'Selected backups provide rollback. Type DELETE BACKUPS to remove these selected backups'
    if ($taskAllowBackup -cne 'DELETE BACKUPS') { Write-Host 'Nothing deleted.'; return }
    $taskArguments += '--delete-backups'
}
$taskConfirm = Read-Host "Delete only these entries: $($taskNames -join ', ')? Type DELETE"
if ($taskConfirm -cne 'DELETE') { Write-Host 'Nothing deleted.'; return }
& $Python @taskArguments
if ($LASTEXITCODE -ne 0) { throw 'Cleanup stopped; inspect the error and regenerate the plan if files changed.' }
