param(
    [int]$DurationMinutes = 60,
    [int]$IntervalSeconds = 60,
    [string]$Series = "KXHIGHLAX",
    [string]$Station = "KLAX"
)

$ErrorActionPreference = "Stop"
$Root = "C:\Users\jarve\Documents\Codex\kalshi_weather"
Set-Location -LiteralPath $Root

if (Test-Path -LiteralPath ".\.venv\Scripts\Activate.ps1") {
    . .\.venv\Scripts\Activate.ps1
}

New-Item -ItemType Directory -Path "logs\automation" -Force | Out-Null
$Log = "logs\automation\collect_session_lax_$(Get-Date -Format yyyyMMdd_HHmmss).log"

kalshi-weather collect-session `
    --series $Series `
    --station $Station `
    --interval-seconds $IntervalSeconds `
    --duration-minutes $DurationMinutes *>&1 | Tee-Object -FilePath $Log
