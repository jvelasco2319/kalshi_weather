param(
  [string]$Series = "KXHIGHLAX",
  [string]$Station = "KLAX",
  [string]$TargetDate = "",
  [string]$DebugRoot = "C:\Users\jarve\Documents\Codex\kalshi_weather\reports\trader_agent\debug",
  [string]$ProfileConfig = "configs/trader_time_profiles_lifecycle.yaml",
  [string]$ProbabilityBlendConfig = "configs/probability_blend_defaults.yaml",
  [string]$CycleMode = "continuous",
  [int]$MaxCycles = 0,
  [int]$PollSeconds = 60,
  [double]$StartingCash = 1000,
  [switch]$AllowMetadataFallbackTimes,
  [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$runId = "$($Series.ToLower())_lifecycle_$stamp"
$debugDir = Join-Path $DebugRoot $runId
New-Item -ItemType Directory -Force -Path $debugDir | Out-Null

$terminalLog = Join-Path $debugDir "terminal_output.txt"
Start-Transcript -Path $terminalLog -Force

try {
  $cliArgs = @(
    "trader-market-cycle",
    "--series", $Series,
    "--station", $Station,
    "--race-id", $runId,
    "--decision-mode", "rules",
    "--strategy", "hybrid",
    "--order-style", "passive",
    "--paper-fill-price-mode", "conservative",
    "--profile-mode", "auto",
    "--profile-config", $ProfileConfig,
    "--probability-blend-mode", "blend",
    "--probability-blend-config", $ProbabilityBlendConfig,
    "--cycle-mode", $CycleMode,
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

  if ($MaxCycles -gt 0) {
    $cliArgs += @("--max-cycles", "$MaxCycles")
  }

  if (![string]::IsNullOrWhiteSpace($TargetDate)) {
    $cliArgs += @("--target-date", $TargetDate)
  }

  if ($AllowMetadataFallbackTimes) {
    $cliArgs += "--allow-metadata-fallback-times"
  }

  if ($DryRun) {
    $cliArgs += "--dry-run"
  }

  & kalshi-weather @cliArgs
}
finally {
  Stop-Transcript
  Write-Host ""
  Write-Host "Lifecycle run:"
  Write-Host "Run ID: $runId"
  Write-Host "Debug dir: $debugDir"
  Write-Host "Lifecycle state: $(Join-Path $debugDir 'lifecycle_state.json')"
  Write-Host "Journal: $(Join-Path $debugDir 'diagnostic.sqlite')"
  Write-Host "Terminal log: $terminalLog"
}
