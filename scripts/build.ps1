[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
  & python -m venv (Join-Path $projectRoot '.venv')
  if ($LASTEXITCODE -ne 0) { throw 'Failed to create build environment' }
}
& $python -m pip install -r (Join-Path $projectRoot 'requirements-build.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
Push-Location $projectRoot
try {
  & $python scripts/make_icon.py
  if ($LASTEXITCODE -ne 0) { throw 'Icon generation failed' }
  & $python -m PyInstaller --noconfirm --windowed --onedir --name DoubleCtrlTranslator --icon build/doublectrltranslator.ico --exclude-module tkinter --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets --exclude-module PySide6.QtPdf main.py
  if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
} finally { Pop-Location }
Write-Host "Built: $projectRoot\dist\DoubleCtrlTranslator\DoubleCtrlTranslator.exe"
$exe = Join-Path $projectRoot 'dist\DoubleCtrlTranslator\DoubleCtrlTranslator.exe'
$menu = [Environment]::GetFolderPath('Programs')
$shell = New-Object -ComObject WScript.Shell
$oldLink = Join-Path $menu 'HermesTool.lnk'
$oldExe = Join-Path $env:LOCALAPPDATA 'Programs\HermesTool\HermesTool.exe'
if ((Test-Path -LiteralPath $oldLink) -and $shell.CreateShortcut($oldLink).TargetPath -eq $oldExe) {
  Remove-Item -LiteralPath $oldLink
}
$shortcut = $shell.CreateShortcut((Join-Path $menu 'DoubleCtrlTranslator.lnk'))
$shortcut.TargetPath = $exe
$shortcut.WorkingDirectory = Split-Path -Parent $exe
$shortcut.IconLocation = $exe
$shortcut.Save()
