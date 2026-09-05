[CmdletBinding()]
param([switch]$Uninstall)

$ErrorActionPreference = 'Stop'
$serviceName = 'RecommendRadioBgeM3'
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$serviceScript = Join-Path (Split-Path -Parent $scriptRoot) 'backend\embedding_windows_service.py'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $isAdmin) { throw 'Run this script from an elevated PowerShell window.' }

if ($Uninstall) {
    if (Get-Service -Name $serviceName -ErrorAction SilentlyContinue) {
        & (Join-Path $env:WINDIR 'py.exe') -3.12 $serviceScript stop 2>$null
        & (Join-Path $env:WINDIR 'py.exe') -3.12 $serviceScript remove
    }
    Unregister-ScheduledTask -TaskName 'RecommendRadio-BgeM3' -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed $serviceName"
    exit 0
}

Unregister-ScheduledTask -TaskName 'RecommendRadio-BgeM3' -Confirm:$false -ErrorAction SilentlyContinue
$existing = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
if ($existing) {
    if ($existing.Status -ne 'Stopped') {
        Stop-Service -Name $serviceName -Force
        (Get-Service -Name $serviceName).WaitForStatus('Stopped', (New-TimeSpan -Seconds 30))
    }
    & (Join-Path $env:WINDIR 'py.exe') -3.12 $serviceScript --startup auto update
} else {
    & (Join-Path $env:WINDIR 'py.exe') -3.12 $serviceScript --startup auto install
}
if ($LASTEXITCODE -ne 0) { throw "Failed to install or update $serviceName" }

$listeners = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue
foreach ($listener in $listeners) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)" -ErrorAction SilentlyContinue
    $isEmbeddingProcess = $false
    if ($process) {
        $isEmbeddingProcess = (
            $process.CommandLine -like '*embedding_server:app*' -or
            $process.CommandLine -like '*embedding_windows_service.py*' -or
            ($process.Name -eq 'pythonservice.exe' -and $process.CommandLine -like '*RecommendRadioBgeM3*')
        )
    }
    if ($isEmbeddingProcess) {
        Stop-Process -Id $listener.OwningProcess -Force
    }
}
Start-Service -Name $serviceName
$deadline = (Get-Date).AddSeconds(120)
do {
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8001/health' -TimeoutSec 3
        if ($health.status -eq 'ready') {
            Write-Host "Installed and started $serviceName ($($health.model))"
            exit 0
        }
    } catch {}
    Start-Sleep -Seconds 2
} while ((Get-Date) -lt $deadline)
throw "$serviceName was installed but its health endpoint did not become ready."
