$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$statePath = Join-Path $projectRoot 'data\processes.json'
if (-not (Test-Path -LiteralPath $statePath)) { Write-Output '没有保存的进程记录。'; exit 0 }
$state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
foreach ($processId in @($state.api, $state.worker)) {
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $processId"
    if ($processInfo -and $processInfo.ExecutablePath -eq $state.executable -and $processInfo.CommandLine -match 'backend\.(app|worker)') {
        Get-CimInstance Win32_Process -Filter "ParentProcessId = $processId" | Where-Object { $_.CommandLine -match 'backend\.(app|worker)' } | ForEach-Object { Stop-Process -Id $_.ProcessId }
        Stop-Process -Id $processId
        Write-Output "已停止 $processId"
    }
}
