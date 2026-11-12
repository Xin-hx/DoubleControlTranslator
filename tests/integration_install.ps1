$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$sandbox = Join-Path $projectRoot '.install-test-home'
if (Test-Path -LiteralPath $sandbox) { Remove-Item -LiteralPath $sandbox -Recurse -Force }
New-Item -ItemType Directory -Path (Join-Path $sandbox 'plugins') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $sandbox 'desktop-plugins') -Force | Out-Null
try {
  & (Join-Path $projectRoot 'scripts\install.ps1') -HermesHome $sandbox
  $backend = Join-Path $sandbox 'plugins\selection-translate'
  $desktop = Join-Path $sandbox 'desktop-plugins\selection-translate'
  if (-not (Test-Path -LiteralPath (Join-Path $backend 'hermes_selection_translate\worker.py'))) { throw 'backend package missing' }
  if (-not (Test-Path -LiteralPath (Join-Path $desktop 'plugin.js'))) { throw 'desktop loader missing' }
  $config = Join-Path $backend 'config.json'
  [IO.File]::WriteAllText($config, '{"double_ctrl_ms":420}', (New-Object System.Text.UTF8Encoding($false)))
  $worker = Join-Path $backend 'hermes_selection_translate\worker.py'
  Add-Content -LiteralPath $worker -Value '# sandbox-stale-copy'
  $rollbackExercised = $false
  try { & (Join-Path $projectRoot 'scripts\update.ps1') -HermesHome $sandbox -TestFailAfterBackendSwap } catch { $rollbackExercised = $true }
  if (-not $rollbackExercised -or -not (Select-String -LiteralPath $worker -Pattern 'sandbox-stale-copy' -Quiet)) { throw 'update rollback did not restore backend' }
  $rollbackFailureExercised = $false
  try { & (Join-Path $projectRoot 'scripts\update.ps1') -HermesHome $sandbox -TestFailAfterBackendSwap -TestFailRollback } catch { $rollbackFailureExercised = $_.Exception.Message -like '*Backup retained at:*' }
  $retained = Get-ChildItem -LiteralPath $sandbox -Directory -Filter '.selection-translate-update-*' | Select-Object -First 1
  if (-not $rollbackFailureExercised -or -not $retained) { throw 'rollback failure did not retain its transaction' }
  $retainedConfig = Join-Path $retained.FullName 'backup\backend\config.json'
  if (-not (Test-Path -LiteralPath $retainedConfig) -or (Get-Content -Raw -LiteralPath $retainedConfig).Trim() -ne '{"double_ctrl_ms":420}') { throw 'rollback failure lost the original config backup' }
  Remove-Item -LiteralPath $backend -Recurse -Force
  Move-Item -LiteralPath (Join-Path $retained.FullName 'backup\backend') -Destination $backend
  Remove-Item -LiteralPath $retained.FullName -Recurse -Force
  & (Join-Path $projectRoot 'scripts\update.ps1') -HermesHome $sandbox
  if ((Get-Content -Raw -LiteralPath $config).Trim() -ne '{"double_ctrl_ms":420}') { throw 'update did not preserve config' }
  if (Select-String -LiteralPath $worker -Pattern 'sandbox-stale-copy' -Quiet) { throw 'update did not refresh project source' }
  $duplicateRejected = $false
  try { & (Join-Path $projectRoot 'scripts\install.ps1') -HermesHome $sandbox } catch { $duplicateRejected = $true }
  if (-not $duplicateRejected) { throw 'duplicate install was not rejected' }
  Set-Content -LiteralPath (Join-Path $desktop '.hermes-selection-translate-install') -Value 'wrong-owner'
  $mismatchRejected = $false
  try { & (Join-Path $projectRoot 'scripts\uninstall.ps1') -HermesHome $sandbox } catch { $mismatchRejected = $true }
  if (-not $mismatchRejected) { throw 'marker mismatch was not rejected' }
  if (-not (Test-Path -LiteralPath $backend) -or -not (Test-Path -LiteralPath $desktop)) { throw 'mismatch removed content' }
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [IO.File]::WriteAllText((Join-Path $desktop '.hermes-selection-translate-install'), 'HermesTool/selection-translate/v1', $utf8NoBom)
  & (Join-Path $projectRoot 'scripts\uninstall.ps1') -HermesHome $sandbox
  if ((Test-Path -LiteralPath $backend) -or (Test-Path -LiteralPath $desktop)) { throw 'uninstall left targets' }
  [pscustomobject]@{ ok = $true; installed_both = $true; update_rollback_exercised = $true; rollback_failure_backup_retained = $true; update_preserved_config = $true; duplicate_rejected = $true; marker_mismatch_rejected = $true; uninstalled_both = $true } | ConvertTo-Json -Compress
} finally {
  if (Test-Path -LiteralPath $sandbox) { Remove-Item -LiteralPath $sandbox -Recurse -Force }
}
