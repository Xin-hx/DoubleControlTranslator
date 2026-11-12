function Assert-ChildPath([string]$Path, [string]$Root) {
  $absolute = [IO.Path]::GetFullPath($Path).TrimEnd('\')
  $boundary = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
  if (-not $absolute.StartsWith($boundary, [StringComparison]::OrdinalIgnoreCase)) { throw "Path escaped intended directory: $absolute" }
  $current = $absolute
  while ($current.Length -ge $boundary.Length) {
    if (Test-Path -LiteralPath $current) {
      if ((Get-Item -LiteralPath $current -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing reparse point: $current" }
    }
    $current = Split-Path -Parent $current
  }
}
function Remove-ManagedDirectory([string]$Path, [string]$Root) {
  Assert-ChildPath $Path $Root
  if (Test-Path -LiteralPath $Path) { Remove-Item -LiteralPath $Path -Recurse -Force }
}
function Move-ManagedDirectory([string]$Source, [string]$Destination, [string]$Root) {
  Assert-ChildPath $Source $Root
  Assert-ChildPath $Destination $Root
  Move-Item -LiteralPath $Source -Destination $Destination
}
