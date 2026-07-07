param(
    [string]$Series = "KXHIGHLAX",
    [string]$Station = "KLAX"
)

$ErrorActionPreference = "Continue"
$Root = "C:\Users\jarve\Documents\Codex\kalshi_weather"
Set-Location -LiteralPath $Root

if (Test-Path -LiteralPath ".\.venv\Scripts\Activate.ps1") {
    . .\.venv\Scripts\Activate.ps1
}

New-Item -ItemType Directory -Path "logs\automation" -Force | Out-Null
$Log = "logs\automation\after_settlement_lax_$(Get-Date -Format yyyyMMdd_HHmmss).log"

& {
    kalshi-weather fetch-missing-outcomes --station $Station
    kalshi-weather join-outcomes --station $Station --overwrite
    kalshi-weather calibration-report --station $Station
    kalshi-weather residual-report --station $Station
    kalshi-weather paper-replay --series $Series --station $Station
    kalshi-weather model-health --series $Series --station $Station
} *>&1 | Tee-Object -FilePath $Log
