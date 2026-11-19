[CmdletBinding()]
param([string]$HermesHome = "$env:LOCALAPPDATA\hermes", [string]$InstallDir = "$env:LOCALAPPDATA\Programs\DoubleCtrlTranslator", [switch]$NoLaunch)
& (Join-Path $PSScriptRoot 'install.ps1') -HermesHome $HermesHome -InstallDir $InstallDir -NoLaunch:$NoLaunch
