param([int]$Port = 8770, [switch]$NoBrowser, [switch]$NoRefresh, [switch]$NoTracking)
$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectDir
$env:PYTHONPATH = 'src;.'
$env:PYTHONDONTWRITEBYTECODE = '1'
$venvPython = Join-Path $projectDir '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) { throw 'Run .\scripts\setup_v10_dashboard.ps1 first.' }
& $venvPython (Join-Path $PSScriptRoot 'prepare_v10_dashboard.py')
if ($LASTEXITCODE -ne 0) { throw 'Frozen V10 verification failed; the dashboard was not started.' }
& $venvPython -m v10_online --project-root $projectDir register
if ($LASTEXITCODE -ne 0) { throw 'Prospective V10 registration failed; the dashboard was not started.' }
$dashboardArgs = @('-m', 'v10_ui', '--root', $projectDir, '--port', "$Port")
if (-not $NoBrowser) { $dashboardArgs += '--open' }
if (-not $NoRefresh) { $dashboardArgs += '--refresh' }
if ($NoTracking) { $dashboardArgs += '--no-tracking' }
& $venvPython @dashboardArgs
exit $LASTEXITCODE
