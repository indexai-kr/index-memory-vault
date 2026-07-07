$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
python -m pip install ".[test]" "mcp[cli]>=1.2" pyinstaller
python -m pytest -q
python -m PyInstaller --clean --noconfirm imv-server.spec
python -m PyInstaller --clean --noconfirm imv-cli.spec
$iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if (-not $iscc) {
  $local = Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'
  if (-not (Test-Path $local)) { throw 'Inno Setup 6 (ISCC.exe) is required.' }
  $isccPath = $local
} else { $isccPath = $iscc.Source }
& $isccPath installer\imv-setup.iss
Get-FileHash dist\imv-server.exe, dist\imv.exe, dist\imv-setup-0.2.1.exe -Algorithm SHA256 |
  Format-Table Path, Hash -AutoSize
