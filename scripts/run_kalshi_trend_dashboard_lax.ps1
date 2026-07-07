$ErrorActionPreference = "Stop"

$Root = "C:\Users\jarve\Documents\Codex\kalshi_weather"
$LogDir = Join-Path $Root "logs\kalshi_history"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogPath = Join-Path $LogDir "kalshi_trend_dashboard_$Stamp.log"

Set-Location -LiteralPath $Root
if (Test-Path -LiteralPath ".\.venv\Scripts\Activate.ps1") {
    . ".\.venv\Scripts\Activate.ps1"
}

$Date = Get-Date -Format "yyyy-MM-dd"

"Kalshi trend dashboard started $(Get-Date -Format o)" | Tee-Object -FilePath $LogPath
"Analysis-only: no live orders, no authenticated trading." | Tee-Object -FilePath $LogPath -Append
kalshi-weather kalshi-trend-dashboard --series KXHIGHLAX --station KLAX --date $Date --backfill-if-missing --period-interval 1 --output-dir "reports\kalshi_trends" 2>&1 |
    Tee-Object -FilePath $LogPath -Append
"Kalshi trend dashboard finished $(Get-Date -Format o)" | Tee-Object -FilePath $LogPath -Append
