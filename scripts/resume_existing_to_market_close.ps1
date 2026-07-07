param(
  [Parameter(Mandatory = $true)]
  [string]$ExistingRunId,
  [string]$Series = "KXHIGHLAX",
  [string]$Station = "KLAX",
  [string]$TargetDate = "",
  [string]$DebugRoot = "reports/trader_agent/lifecycle",
  [string]$EndLocalTime = "23:59",
  [int]$PollSeconds = 60,
  [double]$StartingCash = 1000
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$debugDir = Join-Path $DebugRoot $ExistingRunId
New-Item -ItemType Directory -Force -Path $debugDir | Out-Null

$terminalLog = Join-Path $debugDir "terminal_output.txt"
Start-Transcript -Path $terminalLog -Append

try {
  $parts = $EndLocalTime.Split(":")
  if ($parts.Count -ne 2) {
    throw "EndLocalTime must be HH:MM."
  }
  $now = Get-Date
  $end = Get-Date -Hour ([int]$parts[0]) -Minute ([int]$parts[1]) -Second 0
  if ($end -le $now) {
    throw "EndLocalTime $EndLocalTime is already past."
  }
  $seconds = [math]::Ceiling(($end - $now).TotalSeconds)
  $maxCycles = [math]::Max(1, [math]::Ceiling($seconds / $PollSeconds))

  $cliArgs = @(
    "trader-market-cycle",
    "--series", $Series,
    "--station", $Station,
    "--race-id", $ExistingRunId,
    "--cycle-mode", "continuous",
    "--max-cycles", "$maxCycles",
    "--poll-seconds", "$PollSeconds",
    "--starting-cash", "$StartingCash",
    "--no-use-cached-models",
    "--force-model-recompute-every-iteration",
    "--model-refresh-seconds", "0",
    "--noaa-model-mode", "full_recompute_each_iteration",
    "--debug-root", $DebugRoot,
    "--journal-root", $debugDir,
    "--fake-money-only"
  )

  if (![string]::IsNullOrWhiteSpace($TargetDate)) {
    $cliArgs += @("--target-date", $TargetDate)
  }

  & kalshi-weather @cliArgs
}
finally {
  Stop-Transcript
  Write-Host ""
  Write-Host "Resumed lifecycle run:"
  Write-Host "Run ID: $ExistingRunId"
  Write-Host "Debug dir: $debugDir"
  Write-Host "Journal: $(Join-Path $debugDir 'diagnostic.sqlite')"
  Write-Host "Terminal log: $terminalLog"
}
