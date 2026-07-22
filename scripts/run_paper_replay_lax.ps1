$ErrorActionPreference = "Stop"
$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $Root
if (Test-Path -LiteralPath ".\.venv\Scripts\Activate.ps1") {
    . .\.venv\Scripts\Activate.ps1
}
New-Item -ItemType Directory -Path "logs" -Force | Out-Null
$Log = "logs\paper_replay_lax_$(Get-Date -Format yyyyMMdd_HHmmss).log"
kalshi-weather paper-replay --series KXHIGHLAX --station KLAX *>&1 | Tee-Object -FilePath $Log
