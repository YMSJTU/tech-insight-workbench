param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw '请先按 README 安装 Python 依赖并创建 .venv。' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'apps\web\dist\index.html'))) { throw '请先运行 npm run build --prefix apps/web。' }
$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listener) { throw "端口 $Port 已被占用。请先停止已有服务，或通过 -Port 指定其他端口。" }
& $pythonPath -c 'from backend.db import init_db; init_db()'
if ($LASTEXITCODE -ne 0) { throw '数据库初始化失败。' }
$logPath = Join-Path $projectRoot 'data'
New-Item -ItemType Directory -Path $logPath -Force | Out-Null
$apiProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', "$Port") -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logPath 'api.log') -RedirectStandardError (Join-Path $logPath 'api-error.log') -PassThru
$workerProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-m', 'backend.worker') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logPath 'worker.log') -RedirectStandardError (Join-Path $logPath 'worker-error.log') -PassThru
@{ api = $apiProcess.Id; worker = $workerProcess.Id; port = $Port; executable = $pythonPath } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logPath 'processes.json') -Encoding utf8
Write-Output "工作台：http://127.0.0.1:$Port"
Write-Output "API PID: $($apiProcess.Id); Worker PID: $($workerProcess.Id)"
