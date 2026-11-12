[CmdletBinding()]
param(
  [string]$HermesHome = "$env:LOCALAPPDATA\hermes",
  [string]$InstallDir = "$env:LOCALAPPDATA\Programs\HermesTool",
  [switch]$NoLaunch
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'paths.ps1')
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$source = Join-Path $projectRoot 'dist\HermesTool'
$installRoot = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
$appParent = Split-Path -Parent $installRoot
$hermesRoot = [IO.Path]::GetFullPath($HermesHome).TrimEnd('\')
Assert-ChildPath $installRoot $appParent
$markerName = '.hermes-selection-translate-install'
$oldMarker = 'HermesTool/selection-translate/v1'
$newMarker = 'HermesTool/selection-translate/v2'
$appMarker = '.hermestool-install'
if (-not (Test-Path -LiteralPath (Join-Path $source 'HermesTool.exe'))) { throw 'Run scripts/build.ps1 first' }
if (Get-Process -Name HermesTool -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($installRoot + '\', [StringComparison]::OrdinalIgnoreCase) }) { throw 'Exit HermesTool from its tray menu before updating' }
if (Test-Path -LiteralPath $installRoot) {
  $marker = Join-Path $installRoot $appMarker
  if (-not (Test-Path -LiteralPath $marker) -or (Get-Content -Raw -LiteralPath $marker).Trim() -ne $newMarker) { throw 'Refusing to overwrite an unmarked application directory' }
}
$targets = @()
if (Test-Path -LiteralPath $hermesRoot -PathType Container) {
  foreach ($relative in @('plugins\selection-translate', 'desktop-plugins\selection-translate')) {
    $target = [IO.Path]::GetFullPath((Join-Path $hermesRoot $relative))
    Assert-ChildPath $target $hermesRoot
    if (Test-Path -LiteralPath $target) {
      $marker = Join-Path $target $markerName
      if (-not (Test-Path -LiteralPath $marker) -or (Get-Content -Raw -LiteralPath $marker).Trim() -notin @($oldMarker,$newMarker)) { throw "Refusing to replace unowned plugin: $target" }
    }
    $targets += $target
  }
}
$stamp = [guid]::NewGuid().ToString('N')
$stage = $installRoot + '.stage-' + $stamp
$backup = $installRoot + '.backup-' + $stamp
$utf8 = New-Object System.Text.UTF8Encoding($false)
New-Item -ItemType Directory -Path $stage -Force | Out-Null
Copy-Item -Path (Join-Path $source '*') -Destination $stage -Recurse -Force
[IO.File]::WriteAllText((Join-Path $stage $appMarker), $newMarker, $utf8)
$swapped = $false
$pluginBackups = @()
$dataRoot = if ($env:HERMESTOOL_HOME) { $env:HERMESTOOL_HOME } else { Join-Path $env:LOCALAPPDATA 'HermesTool' }
$metadataBackups = @()
foreach ($path in @((Join-Path $hermesRoot 'config.yaml'),(Join-Path $dataRoot 'settings.json'),(Join-Path $dataRoot 'bridge.json'))) {
  $metadataBackups += [pscustomobject]@{ Path=$path; Bytes=$(if (Test-Path -LiteralPath $path) { [IO.File]::ReadAllBytes($path) } else { $null }) }
}
try {
  if (Test-Path -LiteralPath $installRoot) { Move-ManagedDirectory $installRoot $backup $appParent }
  Move-ManagedDirectory $stage $installRoot $appParent
  $swapped = $true
  $exe = Join-Path $installRoot 'HermesTool.exe'
  $metadata = Start-Process -FilePath $exe -ArgumentList @('--install-metadata','--hermes-home',('"' + $hermesRoot + '"'),'--executable',('"' + $exe + '"')) -WindowStyle Hidden -Wait -PassThru
  if ($metadata.ExitCode -ne 0) { throw "Metadata migration failed; see $env:LOCALAPPDATA\HermesTool\install-error.log" }
  foreach ($target in $targets) {
    $saved = $target + '.backup-' + $stamp
    if (Test-Path -LiteralPath $target) { Move-ManagedDirectory $target $saved $hermesRoot }
    $pluginBackups += [pscustomobject]@{ Target=$target; Saved=$saved }
    New-Item -ItemType Directory -Path $target -Force | Out-Null
    if ($target -eq $targets[0]) {
      Copy-Item -LiteralPath (Join-Path $projectRoot 'plugin\plugin.yaml') -Destination $target
      Copy-Item -LiteralPath (Join-Path $projectRoot 'plugin\__init__.py') -Destination $target
      Copy-Item -LiteralPath (Join-Path $projectRoot 'plugin\dashboard') -Destination $target -Recurse
    } else {
      Copy-Item -LiteralPath (Join-Path $projectRoot 'plugin\desktop\plugin.js') -Destination $target
    }
    [IO.File]::WriteAllText((Join-Path $target $markerName), $newMarker, $utf8)
  }
} catch {
  foreach ($entry in $pluginBackups) {
    Remove-ManagedDirectory $entry.Target $hermesRoot
    if (Test-Path -LiteralPath $entry.Saved) { Move-ManagedDirectory $entry.Saved $entry.Target $hermesRoot }
  }
  if ($swapped) { Remove-ManagedDirectory $installRoot $appParent }
  if (Test-Path -LiteralPath $backup) { Move-ManagedDirectory $backup $installRoot $appParent }
  foreach ($entry in $metadataBackups) {
    if ($null -ne $entry.Bytes) { [IO.File]::WriteAllBytes($entry.Path, [byte[]]$entry.Bytes) }
    elseif (Test-Path -LiteralPath $entry.Path) { Remove-Item -LiteralPath $entry.Path }
  }
  throw
}
foreach ($entry in $pluginBackups) { Remove-ManagedDirectory $entry.Saved $hermesRoot }
Remove-ManagedDirectory $backup $appParent
$shell = New-Object -ComObject WScript.Shell
foreach ($folder in @([Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('Programs'))) {
  $shortcut = $shell.CreateShortcut((Join-Path $folder 'HermesTool.lnk'))
  $shortcut.TargetPath = $exe
  $shortcut.WorkingDirectory = $installRoot
  $shortcut.Save()
}
if (-not $NoLaunch) { Start-Process -FilePath $exe -WindowStyle Normal }
Write-Host "Installed independent app: $exe. Restart Hermes to activate the optional startup bridge."
