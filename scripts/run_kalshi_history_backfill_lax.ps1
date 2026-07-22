$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$LogDir = Join-Path $Root "logs\kalshi_history"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogPath = Join-Path $LogDir "kalshi_history_backfill_$Stamp.log"

Set-Location -LiteralPath $Root
if (Test-Path -LiteralPath ".\.venv\Scripts\Activate.ps1") {
    . ".\.venv\Scripts\Activate.ps1"
}

$Date = Get-Date -Format "yyyy-MM-dd"

"Kalshi history backfill started $(Get-Date -Format o)" | Tee-Object -FilePath $LogPath
"Analysis-only: no live orders, no authenticated trading." | Tee-Object -FilePath $LogPath -Append
kalshi-weather kalshi-history-backfill --series KXHIGHLAX --start-date $Date --end-date $Date --period-interval 1 --store --json --output "reports\latest_kalshi_history_backfill.json" 2>&1 |
    Tee-Object -FilePath $LogPath -Append
"Kalshi history backfill finished $(Get-Date -Format o)" | Tee-Object -FilePath $LogPath -Append
