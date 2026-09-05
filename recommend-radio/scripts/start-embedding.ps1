$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$appRoot = Split-Path -Parent $scriptRoot
$backendRoot = Join-Path $appRoot 'backend'
$existing = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue
if ($existing) { exit 0 }
Set-Location $backendRoot
& (Join-Path $env:WINDIR 'py.exe') -3.12 -m uvicorn embedding_server:app --host 127.0.0.1 --port 8001
