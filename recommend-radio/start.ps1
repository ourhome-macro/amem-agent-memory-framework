[CmdletBinding()]
param(
    [switch]$NoBuild,
    [switch]$NoFrontend,
    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'
$launcher = Join-Path $PSScriptRoot 'scripts\start-local.ps1'
$arguments = @{
    Rebuild = -not $NoBuild
    NoFrontend = [bool]$NoFrontend
    ValidateOnly = [bool]$ValidateOnly
}

& $launcher @arguments
