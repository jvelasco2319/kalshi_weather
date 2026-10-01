param(
    [string]$ResearchRoot = "",
    [string]$HistoryFile = "",
    [switch]$UseSample
)
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
$InstallTarget = if ($ResearchRoot) { ".[legacy-import,dev]" } else { ".[dev]" }
& $VenvPython -m pip install -e $InstallTarget
if ($LASTEXITCODE -ne 0) { throw "Installing the project failed. Check the internet connection and rerun setup." }

& $VenvPython -m kalshi_swarm.verify
if ($LASTEXITCODE -ne 0) { throw "Frozen-model verification failed." }
& $VenvPython -m pytest -q --basetemp .test-artifacts -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { throw "Automated tests failed; historical analysis was not started." }

if ($HistoryFile) {
    New-Item -ItemType Directory -Force data | Out-Null
    Copy-Item -LiteralPath $HistoryFile -Destination "data\history.jsonl" -Force
} elseif ($ResearchRoot) {
    & $VenvPython -m kalshi_swarm.legacy_import --research-root $ResearchRoot --output "data\history.jsonl"
    if ($LASTEXITCODE -ne 0) { throw "Importing the full research history failed. Confirm -ResearchRoot and rerun setup." }
} elseif ($UseSample) {
    New-Item -ItemType Directory -Force data | Out-Null
    Copy-Item -LiteralPath "examples\history.sample.jsonl" -Destination "data\history.jsonl" -Force
} else {
    $BundledHistory = "examples\history.public-development.jsonl"
    $ExpectedHash = "61f91baa7f5f7a897ab1b2d1103cc3582351dec4c5ee1750a470b3dff7deac27"
    $ActualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $BundledHistory).Hash.ToLowerInvariant()
    if ($ActualHash -ne $ExpectedHash) { throw "Bundled historical input failed its integrity check." }
    New-Item -ItemType Directory -Force data | Out-Null
    Copy-Item -LiteralPath $BundledHistory -Destination "data\history.jsonl" -Force
    Write-Host "Using the bundled 329-date public-derived forecast history." -ForegroundColor Cyan
}

& $VenvPython -m kalshi_swarm.cli history --input "data\history.jsonl" --output-dir "artifacts\offline"
if ($LASTEXITCODE -ne 0) { throw "Historical analysis failed; setup is incomplete." }
Write-Host "Setup complete. Run .\scripts\run_offline.ps1 whenever you want to rerun the comparison." -ForegroundColor Green

