$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot
$PythonCommand = ""
$PythonPrefix = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3.11 --version *> $null
    if ($LASTEXITCODE -eq 0) { $PythonCommand = "py"; $PythonPrefix = @("-3.11") }
}
if (-not $PythonCommand -and (Get-Command python -ErrorAction SilentlyContinue)) {
    & python --version *> $null
    if ($LASTEXITCODE -eq 0) { $PythonCommand = "python" }
}
if (-not $PythonCommand) { throw "Python 3.11 or newer is required. Install it from python.org, then rerun this script." }
& $PythonCommand @PythonPrefix -m venv .venv
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -e ".[online,dev]"
& $VenvPython -c "import json,pathlib; [json.loads(p.read_text(encoding='utf-8-sig')) for p in pathlib.Path('config').glob('frozen_*.json')]; print('Frozen V5B, V8, and V10 configurations verified.')"
& $VenvPython -m pytest -q --basetemp .test-artifacts
& $VenvPython -m kalshi_swarm.cli snapshot --output-dir "artifacts\online"
Write-Host "Setup complete. Open artifacts\online\dashboard.html or run .\scripts\run_online.ps1." -ForegroundColor Green
