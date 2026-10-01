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
& $VenvPython -m pip install -e ".[legacy-import]"

& $VenvPython -m kalshi_swarm.verify

if ($HistoryFile) {
    New-Item -ItemType Directory -Force data | Out-Null
    Copy-Item -LiteralPath $HistoryFile -Destination "data\history.jsonl" -Force
} elseif ($ResearchRoot) {
    & $VenvPython -m kalshi_swarm.legacy_import --research-root $ResearchRoot --output "data\history.jsonl"
} elseif ($UseSample) {
    New-Item -ItemType Directory -Force data | Out-Null
    Copy-Item -LiteralPath "examples\history.sample.jsonl" -Destination "data\history.jsonl" -Force
} else {
    throw "Provide -ResearchRoot, -HistoryFile, or -UseSample. See README.md step 2."
}

& $VenvPython -m kalshi_swarm.cli history --input "data\history.jsonl" --output-dir "artifacts\offline"
Write-Host "Setup complete. Run .\scripts\run_offline.ps1 whenever you want to rerun the comparison." -ForegroundColor Green

