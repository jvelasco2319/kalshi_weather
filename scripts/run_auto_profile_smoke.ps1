Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

& "$PSScriptRoot\run_canonical_paper.ps1" `
  -Tomorrow `
  -DurationMinutes 20 `
  -IntervalSeconds 60
