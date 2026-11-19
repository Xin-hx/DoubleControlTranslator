[CmdletBinding()]
param([string]$HermesHome = "$env:LOCALAPPDATA\hermes", [string]$InstallDir = "$env:LOCALAPPDATA\Programs\DoubleCtrlTranslator")
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'paths.ps1')
$installRoot = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
$hermesRoot = [IO.Path]::GetFullPath($HermesHome).TrimEnd('\')
$appParent = Split-Path -Parent $installRoot
Assert-ChildPath $installRoot $appParent
$markerText = 'DoubleCtrlTranslator/selection-translate/v2'
$appMarker = Join-Path $installRoot '.doublectrltranslator-install'
if (-not (Test-Path -LiteralPath $appMarker) -or (Get-Content -Raw -LiteralPath $appMarker).Trim() -ne $markerText) { throw 'App install marker mismatch' }
if (Get-Process -Name DoubleCtrlTranslator -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($installRoot + '\', [StringComparison]::OrdinalIgnoreCase) }) { throw 'Exit DoubleCtrlTranslator from the tray before uninstalling' }
$targets = @()
foreach ($relative in @('plugins\selection-translate', 'desktop-plugins\selection-translate')) {
  $target = [IO.Path]::GetFullPath((Join-Path $hermesRoot $relative))
  Assert-ChildPath $target $hermesRoot
  if (Test-Path -LiteralPath $target) {
    $marker = Join-Path $target '.hermes-selection-translate-install'
    if (-not (Test-Path -LiteralPath $marker) -or (Get-Content -Raw -LiteralPath $marker).Trim() -ne $markerText) { throw "Plugin marker mismatch: $target" }
    $targets += $target
  }
}
$exe = Join-Path $installRoot 'DoubleCtrlTranslator.exe'
$metadata = Start-Process -FilePath $exe -ArgumentList @('--install-metadata','--uninstall','--hermes-home',('"' + $hermesRoot + '"'),'--executable',('"' + $exe + '"')) -WindowStyle Hidden -Wait -PassThru
if ($metadata.ExitCode -ne 0) { throw 'Failed to remove bridge metadata; nothing deleted' }
foreach ($target in $targets) { Remove-ManagedDirectory $target $hermesRoot }
$shell = New-Object -ComObject WScript.Shell
foreach ($folder in @([Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('Programs'))) {
  $link = Join-Path $folder 'DoubleCtrlTranslator.lnk'
  if ((Test-Path -LiteralPath $link) -and $shell.CreateShortcut($link).TargetPath -eq $exe) { Remove-Item -LiteralPath $link }
}
Remove-ManagedDirectory $installRoot $appParent
Write-Host 'Removed DoubleCtrlTranslator and its bridge. User settings, logs, models and llama.cpp were preserved.'
