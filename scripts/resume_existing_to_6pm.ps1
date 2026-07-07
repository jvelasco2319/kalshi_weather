param(
  [Parameter(Mandatory = $true)]
  [string]$ExistingRunId,

  [Parameter(Mandatory = $true)]
  [string]$TargetDate,

  [string]$Series = "KXHIGHLAX",
  [string]$Station = "KLAX",
  [string]$ProfileConfig = "configs/trader_time_profiles.yaml",
  [int]$IntervalSeconds = 60,
  [string]$EndLocalTime = "18:00"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoJson = & python -c "import json; from kalshi_weather.runtime_paths import get_repo_root; print(json.dumps({'repo_root': str(get_repo_root())}))"
$repoPaths = $repoJson | ConvertFrom-Json
Set-Location $repoPaths.repo_root

$baseJson = & python -c "import json; from kalshi_weather.runtime_paths import canonical_paths_payload; print(json.dumps(canonical_paths_payload('$ExistingRunId')))"
$basePaths = $baseJson | ConvertFrom-Json
$journalPath = $basePaths.journal_path

if (-not (Test-Path -LiteralPath $journalPath)) {
  throw "Existing journal not found: $journalPath"
}

if ($Station.ToUpperInvariant() -eq "KLAX") {
  $timeZoneId = "Pacific Standard Time"
} else {
  $timeZoneId = "Pacific Standard Time"
}

$tz = [System.TimeZoneInfo]::FindSystemTimeZoneById($timeZoneId)
$nowLocal = [System.TimeZoneInfo]::ConvertTimeFromUtc((Get-Date).ToUniversalTime(), $tz)
$parts = $EndLocalTime.Split(":")
if ($parts.Count -ne 2) {
  throw "EndLocalTime must be HH:MM, got '$EndLocalTime'"
}
$endLocal = [datetime]::new(
  $nowLocal.Year,
  $nowLocal.Month,
  $nowLocal.Day,
  [int]$parts[0],
  [int]$parts[1],
  0,
  [System.DateTimeKind]::Unspecified
)

if ($nowLocal -ge $endLocal) {
  Write-Host "Already past $EndLocalTime local station time for $Station. Nothing to resume."
  Write-Host "Current local station time: $($nowLocal.ToString('yyyy-MM-dd HH:mm'))"
  exit 0
}

$durationMinutes = [int][Math]::Ceiling(($endLocal - $nowLocal).TotalMinutes)
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$resumeRunId = "${ExistingRunId}_resume_to_6pm_$stamp"
$resumeJson = & python -c "import json; from kalshi_weather.runtime_paths import canonical_paths_payload; print(json.dumps(canonical_paths_payload('$resumeRunId')))"
$resumePaths = $resumeJson | ConvertFrom-Json
New-Item -ItemType Directory -Force -Path $resumePaths.run_dir | Out-Null

Start-Transcript -Path $resumePaths.terminal_output_path -Force

try {
  Write-Host "Resuming existing paper portfolio"
  Write-Host "Trading race ID: $ExistingRunId"
  Write-Host "Debug run ID: $resumeRunId"
  Write-Host "Target date: $TargetDate"
  Write-Host "Station local time: $($nowLocal.ToString('yyyy-MM-dd HH:mm'))"
  Write-Host "End local time: $EndLocalTime"
  Write-Host "Duration minutes: $durationMinutes"
  Write-Host "Existing journal: $journalPath"
  Write-Host "Continuation debug dir: $($resumePaths.run_dir)"

  $args = @(
    "trader-paper-run",
    "--series", $Series,
    "--station", $Station,
    "--target-date", $TargetDate,
    "--race-id", $ExistingRunId,
    "--debug-run-id", $resumeRunId,
    "--decision-mode", "rules",
    "--strategy", "hybrid",
    "--order-style", "passive",
    "--interval-seconds", "$IntervalSeconds",
    "--duration-minutes", "$durationMinutes",
    "--no-use-cached-models",
    "--force-model-recompute-every-iteration",
    "--model-refresh-seconds", "0",
    "--noaa-model-mode", "full_recompute_each_iteration",
    "--profile-mode", "auto",
    "--profile-config", $ProfileConfig,
    "--probability-blend-mode", "blend",
    "--probability-blend-config", "configs/probability_blend_defaults.yaml",
    "--paper-fill-price-mode", "conservative",
    "--no-allow-scale-in",
    "--show-snapshot", "changed",
    "--snapshot-every", "10",
    "--snapshot-style", "compact",
    "--debug-decision",
    "--explain-hold",
    "--audit-pricing",
    "--audit-portfolio",
    "--audit-data",
    "--show-settlement-scenarios",
    "--settlement-scenario-style", "compact",
    "--show-rejections", "summary",
    "--resume-paper-portfolio",
    "--journal-path", $journalPath,
    "--use-canonical-paths"
  )

  & kalshi-weather @args
}
finally {
  Stop-Transcript

  Write-Host ""
  Write-Host "Resume run complete/stopped:"
  Write-Host "Trading race ID: $ExistingRunId"
  Write-Host "Debug run ID: $resumeRunId"
  Write-Host "Continuation debug dir: $($resumePaths.run_dir)"
  Write-Host "Reused journal: $journalPath"
  Write-Host "latest.json: $($resumePaths.latest_json_path)"
  Write-Host "decisions.jsonl: $($resumePaths.decisions_jsonl_path)"
  Write-Host "candidates.csv: $($resumePaths.candidates_csv_path)"
  Write-Host "terminal_output.txt: $($resumePaths.terminal_output_path)"
  Write-Host "final_results.json: $($resumePaths.final_results_path)"
  Write-Host "bot_trust_report.json: $($resumePaths.bot_trust_report_path)"
  Write-Host ""
  Write-Host "To zip this run:"
  Write-Host ".\scripts\package_debug_run.ps1 -RunId `"$resumeRunId`""
}
