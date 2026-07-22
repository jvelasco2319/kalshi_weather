param(
  [string]$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path,
  [string]$Series = "KXHIGHLAX",
  [string]$Station = "KLAX",
  [string]$TargetDate,
  [string]$RunId,
  [double]$YesStakeDollars = 100,
  [double]$NoStakeDollars = 10,
  [int]$MinNoRangesPerModel = 2,
  [double]$ProfitTargetPct = 0.10,
  [switch]$TakerBuy,
  [switch]$NoCachedModels,
  [switch]$ForceModelRecomputeEveryIteration,
  [switch]$ShowDashboard,
  [int]$DashboardPort = 8765,
  [int]$DashboardRefreshSeconds = 5,
  [int]$IntervalSeconds = 60,
  [string]$NoaaModelMode = "full_recompute_each_iteration"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location $RepoRoot
$env:PYTHONPATH = Join-Path $RepoRoot "src"

if (-not $TargetDate) {
  $TargetDate = (Get-Date).ToString("yyyy-MM-dd")
}
if (-not $RunId) {
  $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
  $RunId = "model_tournament_${Series}_${TargetDate}_$stamp".ToLower() -replace "[^a-z0-9_.-]", "_"
}

$debugDir = Join-Path $RepoRoot "reports\trader_agent\debug\$RunId"
New-Item -ItemType Directory -Force -Path $debugDir | Out-Null

$terminalLog = Join-Path $debugDir "terminal_output.txt"
Start-Transcript -Path $terminalLog -Force

$dashboardStarted = $false

try {
  Write-Host "Starting LAX model tournament supervisor"
  Write-Host "Repo root: $RepoRoot"
  Write-Host "Run ID: $RunId"
  Write-Host "Target date: $TargetDate"
  Write-Host "Output: $debugDir"
  Write-Host "Mode: fake-money-only | taker-style simulated fills | no real orders"
  Write-Host "Press Ctrl+C to stop."

  while ($true) {
    $argsList = @(
      "model-tournament-run",
      "--series", $Series,
      "--station", $Station,
      "--target-date", $TargetDate,
      "--run-id", $RunId,
      "--yes-stake-dollars", $YesStakeDollars,
      "--no-stake-dollars", $NoStakeDollars,
      "--min-no-ranges-per-model", $MinNoRangesPerModel,
      "--profit-target-pct", $ProfitTargetPct,
      "--interval-seconds", $IntervalSeconds,
      "--max-iterations", 1,
      "--noaa-model-mode", $NoaaModelMode,
      "--dashboard-refresh-seconds", $DashboardRefreshSeconds
    )

    if ($NoCachedModels -or $TakerBuy) {
      $argsList += "--no-cached-models"
    }
    if ($ForceModelRecomputeEveryIteration -or $NoCachedModels -or $TakerBuy) {
      $argsList += "--force-model-recompute-every-iteration"
    }
    if ($ShowDashboard) {
      $argsList += "--show-dashboard"
      $argsList += "--dashboard-port"
      $argsList += $DashboardPort
    }

    python -m kalshi_weather.cli @argsList

    if ($ShowDashboard -and -not $dashboardStarted) {
      $dashboardCommand = "Set-Location '$RepoRoot'; `$env:PYTHONPATH = Join-Path '$RepoRoot' 'src'; python -m kalshi_weather.cli model-tournament-dashboard --run-id '$RunId' --port $DashboardPort"
      Start-Process powershell -WindowStyle Hidden -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $dashboardCommand)
      $dashboardStarted = $true
      Write-Host "Dashboard URL: http://127.0.0.1:$DashboardPort/dashboard.html"
    }

    Write-Host "Next cycle in $IntervalSeconds seconds..."
    Start-Sleep -Seconds $IntervalSeconds
  }
}
finally {
  Stop-Transcript
  Write-Host "Model tournament stopped."
  Write-Host "Output: $debugDir"
  Write-Host "Dashboard file: $(Join-Path $debugDir 'dashboard.html')"
}
