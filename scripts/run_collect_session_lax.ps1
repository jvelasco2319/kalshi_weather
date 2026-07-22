$ErrorActionPreference = "Stop"
$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $Root
if (Test-Path -LiteralPath ".\.venv\Scripts\Activate.ps1") {
    . .\.venv\Scripts\Activate.ps1
}
New-Item -ItemType Directory -Path "logs" -Force | Out-Null
$Log = "logs\collect_session_lax_$(Get-Date -Format yyyyMMdd_HHmmss).log"
kalshi-weather collect-session --series KXHIGHLAX --station KLAX --interval-seconds 60 --duration-minutes 60 *>&1 | Tee-Object -FilePath $Log
