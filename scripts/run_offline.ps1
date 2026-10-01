param([string]$HistoryFile = "data\history.jsonl", [switch]$NoOpen)
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) { throw "Run .\scripts\setup_offline.ps1 first." }
& $Python -m kalshi_swarm.cli history --input $HistoryFile --output-dir "artifacts\offline"
if ($LASTEXITCODE -ne 0) { throw "Historical analysis failed; no report was opened." }
if (-not $NoOpen) { Start-Process (Resolve-Path "artifacts\offline\report.html") }

