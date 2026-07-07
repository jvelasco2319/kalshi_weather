param(
  [string]$DebugDir = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($DebugDir)) {
  kalshi-weather trader-debug-last
  kalshi-weather trader-clv-report
  kalshi-weather trader-audit-journal
} else {
  $journalPath = Join-Path $DebugDir "diagnostic.sqlite"
  kalshi-weather trader-debug-last --debug-output-dir $DebugDir
  kalshi-weather trader-clv-report --journal-path $journalPath
  kalshi-weather trader-audit-journal --journal-path $journalPath
}
