param(
  [string]$RepoRoot = "C:\Users\jarve\Documents\Codex\kalshi_weather",
  [string]$Series = "KXHIGHLAX",
  [string]$Station = "KLAX",
  [string]$CurrentTargetDate = "",
  [string]$NextTargetDate = "",
  [int]$CurrentDashboardPort = 8766,
  [int]$NextDashboardPort = 8767,
  [int]$TabbedDashboardPort = 8768,
  [int]$IntervalSeconds = 60,
  [int]$DashboardRefreshSeconds = 5,
  [int]$CheckEverySeconds = 60,
  [int]$DaysAhead = 1,
  [int]$RetainPastDays = 0,
  [string]$NoaaModelMode = "full_recompute_each_iteration",
  [switch]$TakerBuy,
  [switch]$NoCachedModels,
  [switch]$ForceModelRecomputeEveryIteration,
  [switch]$NoAutoRollDates,
  [switch]$KeepProcessesOnExit
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ManagerStamp = Get-Date -Format "yyyyMMdd_HHmmss"

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

function Convert-ToRunToken {
  param([string]$Value)
  return ($Value.ToLowerInvariant() -replace "[^a-z0-9_.-]", "_")
}

function Get-DateRelation {
  param([string]$TargetDate)

  $target = [datetime]::Parse($TargetDate).Date
  $today = (Get-Date).Date
  $delta = ($target - $today).Days

  if ($delta -eq 0) { return "today" }
  if ($delta -eq 1) { return "tomorrow" }
  if ($delta -eq -1) { return "yesterday" }
  if ($delta -gt 1) { return "${delta}d ahead" }
  return "$(-1 * $delta)d ago"
}

function Get-DesiredTargetDates {
  $dates = [System.Collections.Generic.List[string]]::new()

  if ($NoAutoRollDates) {
    if ([string]::IsNullOrWhiteSpace($CurrentTargetDate)) {
      $dates.Add((Get-Date).ToString("yyyy-MM-dd"))
    } else {
      $dates.Add(([datetime]::Parse($CurrentTargetDate)).ToString("yyyy-MM-dd"))
    }

    if ([string]::IsNullOrWhiteSpace($NextTargetDate)) {
      $dates.Add((Get-Date).AddDays(1).ToString("yyyy-MM-dd"))
    } else {
      $dates.Add(([datetime]::Parse($NextTargetDate)).ToString("yyyy-MM-dd"))
    }

    return @($dates | Select-Object -Unique)
  }

  if ($DaysAhead -lt 0) {
    throw "DaysAhead must be zero or greater."
  }
  if ($RetainPastDays -lt 0) {
    throw "RetainPastDays must be zero or greater."
  }

  $today = (Get-Date).Date
  for ($offset = -1 * $RetainPastDays; $offset -le $DaysAhead; $offset++) {
    $dates.Add($today.AddDays($offset).ToString("yyyy-MM-dd"))
  }

  return @($dates | Select-Object -Unique)
}

function Get-NextPort {
  param([hashtable]$Entries)

  $used = [System.Collections.Generic.HashSet[int]]::new()
  [void]$used.Add($TabbedDashboardPort)

  foreach ($entry in $Entries.Values) {
    if ($null -ne $entry.Port) {
      [void]$used.Add([int]$entry.Port)
    }
  }

  $port = [Math]::Min($CurrentDashboardPort, $NextDashboardPort)
  while ($used.Contains($port)) {
    $port++
  }

  return $port
}

function New-TournamentCommand {
  param(
    [string]$TargetDate,
    [string]$RunLabel,
    [int]$DashboardPort,
    [string]$ExistingRunId = ""
  )

  if ([string]::IsNullOrWhiteSpace($ExistingRunId)) {
    $runId = Convert-ToRunToken "model_tournament_${Series}_${TargetDate}_${RunLabel}_${ManagerStamp}"
  } else {
    $runId = $ExistingRunId
  }

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

function Test-ProcessAlive {
  param([object]$ProcessId)

  if ($null -eq $ProcessId) {
    return $false
  }

  try {
    $process = Get-Process -Id ([int]$ProcessId) -ErrorAction Stop
    return (-not $process.HasExited)
  } catch {
    return $false
  }
}

function Start-TournamentEntry {
  param(
    [string]$TargetDate,
    [int]$DashboardPort,
    [string]$ExistingRunId = ""
  )

  $relation = Get-DateRelation -TargetDate $TargetDate
  $runLabel = Convert-ToRunToken ($relation -replace " ", "_")
  $info = New-TournamentCommand -TargetDate $TargetDate -RunLabel $runLabel -DashboardPort $DashboardPort -ExistingRunId $ExistingRunId
  $process = Start-Process powershell -WindowStyle Hidden -PassThru -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $info.Command)

  return [ordered]@{
    Date = $TargetDate
    Relation = $relation
    Title = "$TargetDate ($relation)"
    RunId = $info.RunId
    Url = $info.Url
    Port = $DashboardPort
    ProcessId = $process.Id
    Status = "running"
    StartedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    LastCheckedUtc = (Get-Date).ToUniversalTime().ToString("o")
  }
}

function Stop-TournamentEntry {
  param([System.Collections.IDictionary]$Entry)

  if ($null -eq $Entry) {
    return
  }

  if (Test-ProcessAlive -ProcessId $Entry.ProcessId) {
    Stop-Process -Id ([int]$Entry.ProcessId) -Force -ErrorAction SilentlyContinue
  }
}

function Update-ManagedMarkets {
  param([hashtable]$Entries)

  $desiredDates = @(Get-DesiredTargetDates)

  foreach ($key in @($Entries.Keys)) {
    if ($desiredDates -notcontains $key) {
      Stop-TournamentEntry -Entry $Entries[$key]
      $Entries.Remove($key)
    }
  }

  foreach ($targetDate in $desiredDates) {
    if (-not $Entries.ContainsKey($targetDate)) {
      $port = Get-NextPort -Entries $Entries
      $Entries[$targetDate] = Start-TournamentEntry -TargetDate $targetDate -DashboardPort $port
      continue
    }

    $entry = $Entries[$targetDate]
    $entry.Relation = Get-DateRelation -TargetDate $targetDate
    $entry.Title = "$targetDate ($($entry.Relation))"
    $entry.LastCheckedUtc = (Get-Date).ToUniversalTime().ToString("o")

    if (Test-ProcessAlive -ProcessId $entry.ProcessId) {
      $entry.Status = "running"
    } else {
      $replacement = Start-TournamentEntry -TargetDate $targetDate -DashboardPort ([int]$entry.Port) -ExistingRunId $entry.RunId
      $Entries[$targetDate] = $replacement
    }
  }
}

function Write-Manifest {
  param(
    [hashtable]$Entries,
    [string]$ManifestPath
  )

  $tabs = @(
    $Entries.Values |
      Sort-Object Date |
      ForEach-Object {
        [ordered]@{
          key = $_.Date
          date = $_.Date
          relation = $_.Relation
          title = $_.Title
          run_id = $_.RunId
          url = $_.Url
          port = $_.Port
          process_id = $_.ProcessId
          status = $_.Status
          started_at_utc = $_.StartedAtUtc
          last_checked_utc = $_.LastCheckedUtc
        }
      }
  )

  $manifest = [ordered]@{
    generated_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    series = $Series
    station = $Station
    tabs = $tabs
  }

  $manifest | ConvertTo-Json -Depth 8 | Set-Content -Path $ManifestPath -Encoding UTF8
}

function Write-ProcessFile {
  param(
    [hashtable]$Entries,
    [string]$Path,
    [object]$TabbedServerProcess
  )

  $lines = [System.Collections.Generic.List[string]]::new()
  $lines.Add("LAX model tournament tab manager")
  $lines.Add("Generated: $((Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))")
  $lines.Add("")
  $processIds = [System.Collections.Generic.List[int]]::new()

  if ($null -ne $TabbedServerProcess) {
    $lines.Add("Tabbed dashboard server PID: $($TabbedServerProcess.Id)")
    $lines.Add("Tabbed dashboard URL: http://127.0.0.1:${TabbedDashboardPort}/lax_model_tournament_tabs.html")
    $processIds.Add([int]$TabbedServerProcess.Id)
    $lines.Add("")
  }

  foreach ($entry in ($Entries.Values | Sort-Object Date)) {
    $lines.Add("$($entry.Date) [$($entry.Relation)]")
    $lines.Add("  PID: $($entry.ProcessId)")
    $lines.Add("  URL: $($entry.Url)")
    $lines.Add("  run ID: $($entry.RunId)")
    $lines.Add("  status: $($entry.Status)")
    $processIds.Add([int]$entry.ProcessId)
    $lines.Add("")
  }

  $uniqueIds = @($processIds | Select-Object -Unique)
  if ($uniqueIds.Count -gt 0) {
    $stopCommand = "Stop-Process -Id " + ($uniqueIds -join ",")
    $lines.Add("Stop command:")
    $lines.Add($stopCommand)
  } else {
    $stopCommand = ""
  }

  $lines | Set-Content -Path $Path -Encoding UTF8
  return $stopCommand
}

function Write-TabbedDashboard {
  param([string]$Path)

  $html = @'
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LAX Model Tournament Markets</title>
  <style>
    * { box-sizing: border-box; }
    html, body { height: 100%; }
    body { margin: 0; background: #0f1217; color: #e8edf4; font-family: Segoe UI, Arial, sans-serif; overflow: hidden; }
    header { min-height: 108px; padding: 14px 18px; background: #151a22; border-bottom: 1px solid #2b3444; }
    h1 { margin: 0 0 10px; font-size: 20px; line-height: 1.2; }
    .tabs { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    button { border: 1px solid #354157; border-radius: 6px; background: #202938; color: #e8edf4; padding: 8px 12px; cursor: pointer; font-weight: 700; }
    button.active { background: #235b78; border-color: #52c4ff; }
    button .relation { color: #9fb5d1; font-weight: 600; margin-left: 6px; }
    .meta { margin-top: 10px; color: #9fb5d1; font-size: 13px; display: flex; gap: 14px; flex-wrap: wrap; }
    .meta a { color: #8ee7ff; }
    main { height: calc(100vh - 108px); background: #0f1217; }
    iframe { width: 100%; height: 100%; border: 0; display: none; background: #0f1217; }
    iframe.active { display: block; }
    .empty { padding: 28px; color: #9fb5d1; }
  </style>
</head>
<body>
  <header>
    <h1>LAX Model Tournament Markets</h1>
    <div id="tabs" class="tabs"></div>
    <div class="meta">
      <span id="status">Loading tabs...</span>
      <span id="links"></span>
    </div>
  </header>
  <main id="frames">
    <div class="empty">Waiting for model tournament dashboards...</div>
  </main>
  <script>
    const state = { activeKey: null, knownKeys: new Set() };

    function safeId(value) {
      return String(value).replace(/[^a-zA-Z0-9_-]/g, "_");
    }

    function activate(key) {
      state.activeKey = key;
      for (const button of document.querySelectorAll("[data-tab-key]")) {
        button.classList.toggle("active", button.dataset.tabKey === key);
      }
      for (const frame of document.querySelectorAll("[data-frame-key]")) {
        frame.classList.toggle("active", frame.dataset.frameKey === key);
      }
    }

    function render(manifest) {
      const tabs = manifest.tabs || [];
      const tabRoot = document.getElementById("tabs");
      const frameRoot = document.getElementById("frames");
      const status = document.getElementById("status");
      const links = document.getElementById("links");

      tabRoot.innerHTML = "";
      frameRoot.innerHTML = "";
      links.innerHTML = "";

      if (!tabs.length) {
        status.textContent = "No active market dashboards yet.";
        frameRoot.innerHTML = '<div class="empty">Waiting for model tournament dashboards...</div>';
        state.activeKey = null;
        state.knownKeys = new Set();
        return;
      }

      const newKeys = new Set(tabs.map((tab) => tab.key));
      if (!state.activeKey || !newKeys.has(state.activeKey)) {
        state.activeKey = tabs[0].key;
      }
      state.knownKeys = newKeys;

      for (const tab of tabs) {
        const button = document.createElement("button");
        button.type = "button";
        button.dataset.tabKey = tab.key;
        button.innerHTML = `${tab.date}<span class="relation">${tab.relation || ""}</span>`;
        button.onclick = () => activate(tab.key);
        tabRoot.appendChild(button);

        const frame = document.createElement("iframe");
        frame.dataset.frameKey = tab.key;
        frame.id = "frame-" + safeId(tab.key);
        frame.title = tab.title || tab.date;
        frame.src = tab.url;
        frameRoot.appendChild(frame);
      }

      const generated = manifest.generated_at_utc ? new Date(manifest.generated_at_utc).toLocaleTimeString() : "--";
      status.textContent = `${manifest.series || "KXHIGHLAX"} ${manifest.station || "KLAX"} | tabs: ${tabs.length} | updated ${generated}`;
      links.innerHTML = tabs.map((tab) => `<a href="${tab.url}" target="_blank" rel="noreferrer">${tab.date}</a>`).join(" | ");
      activate(state.activeKey);
    }

    async function refresh() {
      try {
        const response = await fetch("lax_model_tournament_tabs_manifest.json?ts=" + Date.now(), { cache: "no-store" });
        if (!response.ok) {
          throw new Error("manifest " + response.status);
        }
        render(await response.json());
      } catch (error) {
        document.getElementById("status").textContent = "Waiting for tab manifest: " + error.message;
      }
    }

    refresh();
    setInterval(refresh, 15000);
  </script>
</body>
</html>
'@

  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
  Set-Content -Path $Path -Value $html -Encoding UTF8
}

function Start-TabbedDashboardServer {
  param([string]$IndexDir)

  $command = "Set-Location " + (Quote-PSString $IndexDir) + "; python -m http.server $TabbedDashboardPort --bind 127.0.0.1"
  return Start-Process powershell -WindowStyle Hidden -PassThru -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $command)
}

Set-Location $RepoRoot

$ports = @($CurrentDashboardPort, $NextDashboardPort, $TabbedDashboardPort)
if (($ports | Select-Object -Unique).Count -ne $ports.Count) {
  throw "CurrentDashboardPort, NextDashboardPort, and TabbedDashboardPort must be different."
}

$indexDir = Join-Path $RepoRoot "reports\trader_agent\dashboard_tabs"
$indexPath = Join-Path $indexDir "lax_model_tournament_tabs.html"
$manifestPath = Join-Path $indexDir "lax_model_tournament_tabs_manifest.json"
$processPath = Join-Path $indexDir "lax_model_tournament_processes.txt"
New-Item -ItemType Directory -Force -Path $indexDir | Out-Null

Write-TabbedDashboard -Path $indexPath
$tabbedServerProcess = Start-TabbedDashboardServer -IndexDir $indexDir
$entries = @{}

try {
  Write-Host "Starting LAX model tournament tab manager."
  Write-Host "Repo root: $RepoRoot"
  Write-Host "Series: $Series"
  Write-Host "Station: $Station"
  Write-Host "Tabbed dashboard:"
  Write-Host "http://127.0.0.1:${TabbedDashboardPort}/lax_model_tournament_tabs.html"
  Write-Host "Manifest:"
  Write-Host $manifestPath
  Write-Host "Process file:"
  Write-Host $processPath
  Write-Host "Press Ctrl+C to stop the tab manager and child dashboards."
  Write-Host ""

  while ($true) {
    Update-ManagedMarkets -Entries $entries
    Write-Manifest -Entries $entries -ManifestPath $manifestPath
    $stopCommand = Write-ProcessFile -Entries $entries -Path $processPath -TabbedServerProcess $tabbedServerProcess

    $summary = @($entries.Values | Sort-Object Date | ForEach-Object { "$($_.Date):$($_.Port):pid$($_.ProcessId)" }) -join " | "
    Write-Host "$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss')) | market tabs: $($entries.Count) | $summary"
    if ($stopCommand) {
      Write-Host "Stop command: $stopCommand"
    }

    Start-Sleep -Seconds $CheckEverySeconds
  }
}
finally {
  if ($KeepProcessesOnExit) {
    Write-Host "Leaving child dashboards running because -KeepProcessesOnExit was set."
  } else {
    foreach ($entry in $entries.Values) {
      Stop-TournamentEntry -Entry $entry
    }
    if (Test-ProcessAlive -ProcessId $tabbedServerProcess.Id) {
      Stop-Process -Id ([int]$tabbedServerProcess.Id) -Force -ErrorAction SilentlyContinue
    }
  }

  Write-Host "Model tournament tab manager stopped."
  Write-Host "Tabbed dashboard file: $indexPath"
  Write-Host "Manifest: $manifestPath"
  Write-Host "Process file: $processPath"
}
