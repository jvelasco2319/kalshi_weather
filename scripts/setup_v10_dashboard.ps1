param([switch]$NoTests)
$ErrorActionPreference = 'Stop'
$projectDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $projectDir
$pythonCommand = ''
$pythonPrefix = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 --version *> $null
    if ($LASTEXITCODE -eq 0) { $pythonCommand = 'py'; $pythonPrefix = @('-3') }
}
if (-not $pythonCommand -and (Get-Command python -ErrorAction SilentlyContinue)) {
    & python --version *> $null
    if ($LASTEXITCODE -eq 0) { $pythonCommand = 'python' }
}
if (-not $pythonCommand) { throw 'Install 64-bit Python 3.12 or newer from python.org, then rerun setup.' }
$pythonVersion = & $pythonCommand @pythonPrefix -c 'import sys; print(".".join(map(str, sys.version_info[:3])))'
if ([version]$pythonVersion -lt [version]'3.12.0') { throw "V10 needs Python 3.12 or newer; found $pythonVersion." }
& $pythonCommand @pythonPrefix -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Creating the Python environment failed.' }
$venvPython = Join-Path $projectDir '.venv\Scripts\python.exe'
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'Updating pip failed.' }
& $venvPython -m pip install -e '.[online,v10-dashboard,dev]'
if ($LASTEXITCODE -ne 0) { throw 'Installing V10 dashboard dependencies failed.' }
$env:PYTHONPATH = 'src;.'
$env:PYTHONDONTWRITEBYTECODE = '1'
& $venvPython (Join-Path $PSScriptRoot 'prepare_v10_dashboard.py')
if ($LASTEXITCODE -ne 0) { throw 'Frozen V10 verification failed.' }
& $venvPython -m v10_online --project-root $projectDir register
if ($LASTEXITCODE -ne 0) { throw 'Prospective V10 registration failed.' }
& $venvPython -m kalshi_swarm.verify
if ($LASTEXITCODE -ne 0) { throw 'Legacy frozen-method verification failed.' }
if (-not $NoTests) {
    $testDirectory = Join-Path ([IO.Path]::GetTempPath()) ('v10-tests-' + [guid]::NewGuid().ToString('N').Substring(0,8))
    & $venvPython -m pytest -q --basetemp $testDirectory -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) { throw 'Automated tests failed; the dashboard was not started.' }
}
Write-Host 'Setup complete. Run .\scripts\start_v10_dashboard.ps1 to open the dashboard.' -ForegroundColor Green
