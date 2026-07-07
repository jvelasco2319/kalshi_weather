param(
  [string]$RunId = "",
  [string]$TargetDate = "",
  [switch]$Tomorrow,
  [string]$Series = "KXHIGHLAX",
  [string]$Station = "KLAX",
  [double]$DurationMinutes = 240,
  [int]$IntervalSeconds = 60
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $Tomorrow -and [string]::IsNullOrWhiteSpace($TargetDate)) {
  throw "TargetDate is required unless -Tomorrow is supplied."
}

$pathJson = & python -c "import json; from kalshi_weather.runtime_paths import get_repo_root; print(json.dumps({'repo_root': str(get_repo_root())}))"
$paths = $pathJson | ConvertFrom-Json
Set-Location $paths.repo_root

if ([string]::IsNullOrWhiteSpace($RunId)) {
  $datePart = if ($Tomorrow) { "tomorrow" } else { $TargetDate.Replace("-", "") }
  $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
  $RunId = "lax_rules_${datePart}_$stamp"
}

$runPathJson = & python -c "import json; from kalshi_weather.runtime_paths import canonical_paths_payload; print(json.dumps(canonical_paths_payload('$RunId')))"
$runPaths = $runPathJson | ConvertFrom-Json
New-Item -ItemType Directory -Force -Path $runPaths.run_dir | Out-Null

Start-Transcript -Path $runPaths.terminal_output_path -Force

try {
  $args = @(
    "trader-paper-run",
    "--series", $Series,
    "--station", $Station,
    "--race-id", $RunId,
    "--decision-mode", "rules",
    "--strategy", "hybrid",
    "--order-style", "passive",
    "--paper-fill-price-mode", "conservative",
    "--starting-cash", "1000",
    "--interval-seconds", "$IntervalSeconds",
    "--duration-minutes", "$DurationMinutes",
    "--no-use-cached-models",
    "--force-model-recompute-every-iteration",
    "--model-refresh-seconds", "0",
    "--noaa-model-mode", "full_recompute_each_iteration",
    "--min-edge-cents", "8",
    "--min-no-edge-cents", "8",
    "--min-no-upside-cents", "8",
    "--max-no-bin-probability", "0.20",
    "--max-spread-cents", "4",
    "--max-risk-dollars-per-trade", "50",
    "--max-total-exposure-dollars", "250",
    "--max-exposure-dollars-per-bracket", "100",
    "--max-open-positions", "4",
    "--max-open-orders", "4",
    "--max-total-open-risk-groups", "4",
    "--no-allow-scale-in",
    "--same-candidate-cooldown-minutes", "15",
    "--max-passive-distance-from-bid-cents", "1",
    "--max-passive-order-age-minutes", "15",
    "--model-consensus-enabled",
    "--consensus-method", "family_weighted_iqr",
    "--block-high-confidence-no-on-extreme-spread",
    "--show-snapshot", "changed",
    "--snapshot-every", "15",
    "--snapshot-style", "compact",
    "--debug-decision",
    "--explain-hold",
    "--audit-pricing",
    "--audit-portfolio",
    "--audit-data",
    "--show-rejections", "summary",
    "--new-paper-portfolio",
    "--i-understand-this-deletes-paper-state",
    "--use-canonical-paths"
  )

  if ($Tomorrow) {
    $args += "--tomorrow"
  } else {
    $args += @("--target-date", $TargetDate)
  }

  & kalshi-weather @args
}
finally {
  Stop-Transcript

  Write-Host ""
  Write-Host "Run complete:"
  Write-Host "Run ID: $RunId"
  Write-Host "Debug dir: $($runPaths.run_dir)"
  Write-Host "latest.json: $($runPaths.latest_json_path)"
  Write-Host "decisions.jsonl: $($runPaths.decisions_jsonl_path)"
  Write-Host "candidates.csv: $($runPaths.candidates_csv_path)"
  Write-Host "diagnostic.sqlite: $($runPaths.journal_path)"
  Write-Host "terminal_output.txt: $($runPaths.terminal_output_path)"
  Write-Host "final_results.json: $($runPaths.final_results_path)"
  Write-Host "bot_trust_report.json: $($runPaths.bot_trust_report_path)"
  Write-Host ""
  Write-Host "To zip this run:"
  Write-Host ".\scripts\package_debug_run.ps1 -RunId `"$RunId`""
}
