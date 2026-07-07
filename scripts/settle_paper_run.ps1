param(
  [Parameter(Mandatory = $true)]
  [string]$RunId,
  [Parameter(Mandatory = $true)]
  [string]$TargetDate,
  [string]$Series = "KXHIGHLAX",
  [string]$Station = "KLAX",
  [string]$DebugRoot = "reports/trader_agent/lifecycle",
  [double]$StartingCash = 1000,
  [Nullable[double]]$FinalHighF = $null,
  [string]$WinningBracket = "",
  [switch]$ForceResettle
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$debugDir = Join-Path $DebugRoot $RunId
$journalPath = Join-Path $debugDir "diagnostic.sqlite"

$cliArgs = @(
  "trader-settle-paper-run",
  "--race-id", $RunId,
  "--series", $Series,
  "--station", $Station,
  "--target-date", $TargetDate,
  "--journal-path", $journalPath,
  "--starting-cash", "$StartingCash",
  "--settlement-mode", "final_official",
  "--output-dir", $debugDir
)

if ($FinalHighF -ne $null) {
  $cliArgs += @("--final-high-f", "$FinalHighF")
}

if (![string]::IsNullOrWhiteSpace($WinningBracket)) {
  $cliArgs += @("--winning-bracket", $WinningBracket)
}

if ($ForceResettle) {
  $cliArgs += @("--force-resettle", "--i-understand-this-can-change-paper-pnl")
}

& kalshi-weather @cliArgs

Write-Host ""
Write-Host "Settlement files:"
Write-Host "$(Join-Path $debugDir 'settlement_report.json')"
Write-Host "$(Join-Path $debugDir 'settlement_report.txt')"
Write-Host "$(Join-Path $debugDir 'paper_settlement.json')"
