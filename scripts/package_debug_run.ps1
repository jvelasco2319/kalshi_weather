param(
  [string]$RunId = "",
  [switch]$Latest,
  [string]$DebugRoot = (Join-Path $PSScriptRoot "..\reports\trader_agent\debug"),
  [string]$ArchiveRoot = (Join-Path $PSScriptRoot "..\reports\trader_agent\archives"),
  [bool]$IncludeSqlite = $true,
  [bool]$IncludeTerminalLog = $true,
  [bool]$IncludeConfigs = $true,
  [bool]$IncludeReports = $true,
  [bool]$IncludeFinalReports = $true,
  [switch]$OpenFolder
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $Latest -and [string]::IsNullOrWhiteSpace($RunId)) {
  throw "Please supply -RunId or -Latest"
}

$args = @("trader-zip-run")
if ($Latest) {
  $args += "--latest"
} else {
  $args += @("--run-id", $RunId)
}

$args += @(
  "--debug-root", $DebugRoot,
  "--archive-root", $ArchiveRoot
)

if ($IncludeSqlite) { $args += "--include-sqlite" } else { $args += "--no-include-sqlite" }
if ($IncludeTerminalLog) { $args += "--include-terminal-log" } else { $args += "--no-include-terminal-log" }
if ($IncludeConfigs) { $args += "--include-configs" } else { $args += "--no-include-configs" }
if ($IncludeReports) { $args += "--include-reports" } else { $args += "--no-include-reports" }
if ($IncludeFinalReports) { $args += "--include-final-reports" } else { $args += "--no-include-final-reports" }
if ($OpenFolder) { $args += "--open-folder" }

& kalshi-weather @args
