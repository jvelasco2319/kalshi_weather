$ErrorActionPreference = "Stop"
$Root = "C:\Users\jarve\Documents\Codex\kalshi_weather"
Set-Location -LiteralPath $Root
if (Test-Path -LiteralPath ".\.venv\Scripts\Activate.ps1") {
    . .\.venv\Scripts\Activate.ps1
}
New-Item -ItemType Directory -Path "logs" -Force | Out-Null
$Log = "logs\research_status_lax_$(Get-Date -Format yyyyMMdd_HHmmss).log"
kalshi-weather research-status --series KXHIGHLAX --station KLAX *>&1 | Tee-Object -FilePath $Log
