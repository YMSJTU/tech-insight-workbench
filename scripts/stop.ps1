$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$statePath = Join-Path $projectRoot 'data\processes.json'
if (-not (Test-Path -LiteralPath $statePath)) { Write-Output '没有保存的进程记录。'; exit 0 }
$state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
function Stop-OwnedProcessTree([int]$TargetId) {
    Get-CimInstance Win32_Process -Filter "ParentProcessId = $TargetId" | ForEach-Object { Stop-OwnedProcessTree -TargetId $_.ProcessId }
    Stop-Process -Id $TargetId -ErrorAction SilentlyContinue
}
foreach ($processId in @($state.api, $state.worker)) {
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $processId"
    if ($processInfo -and $processInfo.ExecutablePath -eq $state.executable -and $processInfo.CommandLine -match 'backend\.(app|worker)') {
        Stop-OwnedProcessTree -TargetId $processId
        Write-Output "已停止 $processId"
    }
}
