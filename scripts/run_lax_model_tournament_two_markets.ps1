param(
  [string]$RepoRoot = "C:\Users\jarve\Documents\Codex\kalshi_weather",
  [string]$Series = "KXHIGHLAX",
  [string]$Station = "KLAX",
  [string]$CurrentTargetDate = "",
  [string]$NextTargetDate = "",
  [int]$CurrentDashboardPort = 8766,
  [int]$NextDashboardPort = 8767,
  [int]$IntervalSeconds = 60,
  [int]$DashboardRefreshSeconds = 5,
  [string]$NoaaModelMode = "full_recompute_each_iteration",
  [switch]$TakerBuy,
  [switch]$NoCachedModels,
  [switch]$ForceModelRecomputeEveryIteration
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Quote-PSString {
  param([string]$Value)
  return "'" + $Value.Replace("'", "''") + "'"
}

function Add-OptionalSwitch {
  param(
    [System.Collections.Generic.List[string]]$Parts,
    [string]$Name,
    [bool]$Enabled
  )
  if ($Enabled) {
    $Parts.Add($Name)
  }
}

function New-TournamentCommand {
  param(
    [string]$TargetDate,
    [string]$RunLabel,
    [int]$DashboardPort
  )

  $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
  $runId = "model_tournament_${Series}_${TargetDate}_${RunLabel}_${stamp}".ToLower() -replace "[^a-z0-9_.-]", "_"
  $scriptPath = Join-Path $RepoRoot "scripts\run_lax_model_tournament_forever.ps1"
  $parts = [System.Collections.Generic.List[string]]::new()
  $parts.Add("&")
  $parts.Add((Quote-PSString $scriptPath))
  $parts.Add("-RepoRoot")
  $parts.Add((Quote-PSString $RepoRoot))
  $parts.Add("-Series")
  $parts.Add((Quote-PSString $Series))
  $parts.Add("-Station")
  $parts.Add((Quote-PSString $Station))
  $parts.Add("-TargetDate")
  $parts.Add((Quote-PSString $TargetDate))
  $parts.Add("-RunId")
  $parts.Add((Quote-PSString $runId))
  $parts.Add("-ShowDashboard")
  $parts.Add("-DashboardPort")
  $parts.Add("$DashboardPort")
  $parts.Add("-DashboardRefreshSeconds")
  $parts.Add("$DashboardRefreshSeconds")
  $parts.Add("-IntervalSeconds")
  $parts.Add("$IntervalSeconds")
  $parts.Add("-NoaaModelMode")
  $parts.Add((Quote-PSString $NoaaModelMode))
  Add-OptionalSwitch -Parts $parts -Name "-TakerBuy" -Enabled ([bool]$TakerBuy)
  Add-OptionalSwitch -Parts $parts -Name "-NoCachedModels" -Enabled ([bool]$NoCachedModels)
  Add-OptionalSwitch -Parts $parts -Name "-ForceModelRecomputeEveryIteration" -Enabled ([bool]$ForceModelRecomputeEveryIteration)

  return @{
    RunId = $runId
    Command = ($parts -join " ")
    Url = "http://127.0.0.1:${DashboardPort}/dashboard.html"
  }
}

function Write-TabbedDashboard {
  param(
    [string]$Path,
    [hashtable]$Current,
    [hashtable]$Next
  )

  $currentUrl = $Current.Url
  $nextUrl = $Next.Url
  $currentTitle = "Current market $CurrentTargetDate"
  $nextTitle = "Next market $NextTargetDate"
  $html = @"
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LAX Model Tournament Markets</title>
  <style>
    * { box-sizing: border-box; }
    body { margin: 0; background: #0f1217; color: #e8edf4; font-family: Segoe UI, Arial, sans-serif; }
    header { padding: 14px 18px; background: #151a22; border-bottom: 1px solid #2b3444; }
    h1 { margin: 0 0 10px; font-size: 20px; }
    .tabs { display: flex; gap: 8px; flex-wrap: wrap; }
    button { border: 1px solid #354157; border-radius: 6px; background: #202938; color: #e8edf4; padding: 8px 12px; cursor: pointer; font-weight: 700; }
    button.active { background: #235b78; border-color: #52c4ff; }
    .links { margin-top: 10px; color: #9fb5d1; font-size: 13px; }
    a { color: #8ee7ff; }
    main { height: calc(100vh - 110px); }
    iframe { width: 100%; height: 100%; border: 0; display: none; background: #0f1217; }
    iframe.active { display: block; }
  </style>
</head>
<body>
  <header>
    <h1>LAX Model Tournament Markets</h1>
    <div class="tabs">
      <button id="tab-current" class="active" type="button" onclick="showTab('current')">$currentTitle</button>
      <button id="tab-next" type="button" onclick="showTab('next')">$nextTitle</button>
    </div>
    <div class="links">
      Direct links:
      <a href="$currentUrl" target="_blank" rel="noreferrer">current</a>
      |
      <a href="$nextUrl" target="_blank" rel="noreferrer">next</a>
    </div>
  </header>
  <main>
    <iframe id="frame-current" class="active" src="$currentUrl" title="$currentTitle"></iframe>
    <iframe id="frame-next" src="$nextUrl" title="$nextTitle"></iframe>
  </main>
  <script>
    function showTab(name) {
      for (const key of ['current', 'next']) {
        document.getElementById('tab-' + key).classList.toggle('active', key === name);
        document.getElementById('frame-' + key).classList.toggle('active', key === name);
      }
    }
  </script>
</body>
</html>
"@
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
  Set-Content -Path $Path -Value $html -Encoding UTF8
}

Set-Location $RepoRoot

if ($CurrentDashboardPort -eq $NextDashboardPort) {
  throw "CurrentDashboardPort and NextDashboardPort must be different."
}

if ([string]::IsNullOrWhiteSpace($CurrentTargetDate)) {
  $CurrentTargetDate = (Get-Date).ToString("yyyy-MM-dd")
}

if ([string]::IsNullOrWhiteSpace($NextTargetDate)) {
  $NextTargetDate = (Get-Date).AddDays(1).ToString("yyyy-MM-dd")
}

$current = New-TournamentCommand -TargetDate $CurrentTargetDate -RunLabel "current" -DashboardPort $CurrentDashboardPort
$next = New-TournamentCommand -TargetDate $NextTargetDate -RunLabel "next" -DashboardPort $NextDashboardPort

$indexDir = Join-Path $RepoRoot "reports\trader_agent\dashboard_tabs"
$indexPath = Join-Path $indexDir ("lax_model_tournament_tabs_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".html")
Write-TabbedDashboard -Path $indexPath -Current $current -Next $next

Write-Host "Starting two LAX model tournament dashboards."
Write-Host "Current market: $CurrentTargetDate"
Write-Host "  Run ID: $($current.RunId)"
Write-Host "  URL: $($current.Url)"
Write-Host "Next market: $NextTargetDate"
Write-Host "  Run ID: $($next.RunId)"
Write-Host "  URL: $($next.Url)"
Write-Host "Tabbed dashboard file:"
Write-Host $indexPath
Write-Host ""
Write-Host "Each market has its own fake-money state, output folder, and dashboard port."

$currentProcess = Start-Process powershell -WindowStyle Hidden -PassThru -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $current.Command)
$nextProcess = Start-Process powershell -WindowStyle Hidden -PassThru -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $next.Command)
$processPath = Join-Path $indexDir ("lax_model_tournament_processes_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".txt")
@(
  "Current market PID: $($currentProcess.Id)"
  "Current market URL: $($current.Url)"
  "Current market run ID: $($current.RunId)"
  ""
  "Next market PID: $($nextProcess.Id)"
  "Next market URL: $($next.Url)"
  "Next market run ID: $($next.RunId)"
  ""
  "Stop command:"
  "Stop-Process -Id $($currentProcess.Id),$($nextProcess.Id)"
) | Set-Content -Path $processPath -Encoding UTF8

Write-Host "Process file:"
Write-Host $processPath
Write-Host "Stop both loops with:"
Write-Host "Stop-Process -Id $($currentProcess.Id),$($nextProcess.Id)"
