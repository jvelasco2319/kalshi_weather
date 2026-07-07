param(
    [string]$Series = "KXHIGHLAX",
    [string]$Station = "KLAX",
    [string]$Output = "reports\latest_model_health.json"
)

$ErrorActionPreference = "Stop"
$Root = "C:\Users\jarve\Documents\Codex\kalshi_weather"
Set-Location -LiteralPath $Root

if (Test-Path -LiteralPath ".\.venv\Scripts\Activate.ps1") {
    . .\.venv\Scripts\Activate.ps1
}

New-Item -ItemType Directory -Path "logs\automation" -Force | Out-Null
New-Item -ItemType Directory -Path (Split-Path -Parent $Output) -Force | Out-Null
$Log = "logs\automation\model_health_lax_$(Get-Date -Format yyyyMMdd_HHmmss).log"

kalshi-weather model-health `
    --series $Series `
    --station $Station `
    --output $Output `
    --json *>&1 | Tee-Object -FilePath $Log
