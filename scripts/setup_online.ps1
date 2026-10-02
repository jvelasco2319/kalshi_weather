$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot
$PythonCommand = ""
$PythonPrefix = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 --version *> $null
    if ($LASTEXITCODE -eq 0) { $PythonCommand = "py"; $PythonPrefix = @("-3") }
}
if (-not $PythonCommand -and (Get-Command python -ErrorAction SilentlyContinue)) {
    & python --version *> $null
    if ($LASTEXITCODE -eq 0) { $PythonCommand = "python" }
}
if (-not $PythonCommand) { throw "Python 3.11 or newer is required. Install it from python.org, then rerun this script." }
$PythonVersion = & $PythonCommand @PythonPrefix -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
if ([version]$PythonVersion -lt [version]"3.11.0") { throw "Python 3.11 or newer is required; found $PythonVersion." }
& $PythonCommand @PythonPrefix -m venv .venv
if ($LASTEXITCODE -ne 0) { throw "Creating the Python environment failed." }
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Updating pip failed. Check the internet connection and rerun setup." }
& $VenvPython -m pip install -e ".[online,dev]"
if ($LASTEXITCODE -ne 0) { throw "Installing the project failed. Check the internet connection and rerun setup." }
& $VenvPython -m kalshi_swarm.verify
if ($LASTEXITCODE -ne 0) { throw "Frozen-model verification failed." }
& $VenvPython -m pytest -q tests/test_engine.py tests/test_offline.py tests/test_online.py --basetemp .test-artifacts -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { throw "Automated tests failed; no current-data run was started." }
& $VenvPython -m kalshi_swarm.cli snapshot --output-dir "artifacts\online"
if ($LASTEXITCODE -ne 0) { throw "The current-data snapshot failed. Check the public-service connection and rerun setup." }
Write-Host "Setup complete. Open artifacts\online\dashboard.html or run .\scripts\run_online.ps1." -ForegroundColor Green
