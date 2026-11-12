[CmdletBinding()]
param([string]$HermesHome = "$env:LOCALAPPDATA\hermes", [string]$InstallDir = "$env:LOCALAPPDATA\Programs\HermesTool", [switch]$NoLaunch)
& (Join-Path $PSScriptRoot 'install.ps1') -HermesHome $HermesHome -InstallDir $InstallDir -NoLaunch:$NoLaunch
