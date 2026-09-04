[CmdletBinding()]
param(
    [switch]$Rebuild,
    [switch]$NoFrontend
)

$ErrorActionPreference = 'Stop'

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$appRoot = Split-Path -Parent $scriptRoot
$backendRoot = Join-Path $appRoot 'backend'
$embeddingPort = 8001

function Test-HttpReady {
    param([string]$Url)
    try {
        $response = Invoke-RestMethod -Method Get -Uri $Url -TimeoutSec 3
        return $response
    } catch {
        return $null
    }
}

function Wait-HttpReady {
    param([string]$Url, [int]$TimeoutSeconds = 120)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $response = Test-HttpReady -Url $Url
        if ($null -ne $response) { return $response }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    throw "Timed out waiting for $Url"
}

function Start-EmbeddingServer {
    $healthUrl = "http://127.0.0.1:$embeddingPort/health"
    $existing = Test-HttpReady -Url $healthUrl
    if ($existing -and $existing.status -eq 'ready') {
        Write-Host "[embedding] already ready: $($existing.model)" -ForegroundColor Green
        return
    }

    $pythonLauncher = Join-Path $env:WINDIR 'py.exe'
    if (-not (Test-Path $pythonLauncher)) {
        throw 'Python launcher py.exe was not found. Install Python 3.12 first.'
    }
    Write-Host '[embedding] starting local bge-m3 service...' -ForegroundColor Cyan
    Start-Process -FilePath $pythonLauncher `
        -ArgumentList @('-3.12', '-m', 'uvicorn', 'embedding_server:app', '--host', '127.0.0.1', '--port', "$embeddingPort") `
        -WorkingDirectory $backendRoot `
        -WindowStyle Hidden
    $ready = Wait-HttpReady -Url $healthUrl
    if ($ready.status -ne 'ready') { throw 'bge-m3 embedding service did not become ready.' }
    Write-Host "[embedding] ready: $($ready.model)" -ForegroundColor Green
}

function Start-ComposeStack {
    Push-Location $appRoot
    try {
        $services = @('amem', 'backend')
        if (-not $NoFrontend) { $services += 'frontend' }
        $arguments = @('compose', 'up', '-d')
        if ($Rebuild) { $arguments += '--build' }
        $arguments += $services
        Write-Host "[docker] starting: $($services -join ', ')" -ForegroundColor Cyan
        & docker @arguments
        if ($LASTEXITCODE -ne 0) { throw "docker compose failed with exit code $LASTEXITCODE" }
    } finally {
        Pop-Location
    }
}

Start-EmbeddingServer
Start-ComposeStack

$backend = Wait-HttpReady -Url 'http://127.0.0.1:5000/health/ready' -TimeoutSeconds 90
if ($backend.data.status -ne 'ready') { throw 'Recommend Radio backend did not become ready.' }

Write-Host ''
Write-Host 'Recommend Radio is ready.' -ForegroundColor Green
Write-Host '  UI:       http://localhost:3000' -ForegroundColor Green
Write-Host '  Backend:  http://127.0.0.1:5000' -ForegroundColor Green
Write-Host "  Embedding: http://127.0.0.1:$embeddingPort/health" -ForegroundColor Green
