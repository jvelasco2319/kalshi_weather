param(
    [string]$TargetDate = "",
    [switch]$Loop,
    [int]$IntervalSeconds = 60,
    [switch]$NoOpen
)
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) { throw "Run .\scripts\setup_online.ps1 first." }
$CommandName = if ($Loop) { "watch" } else { "snapshot" }
$Arguments = @("-m", "kalshi_swarm.cli", $CommandName, "--output-dir", "artifacts\online")
if ($TargetDate) { $Arguments += @("--date", $TargetDate) }
if ($Loop) { $Arguments += @("--interval-seconds", $IntervalSeconds) }
if ($Loop) {
    $InitialArguments = @("-m", "kalshi_swarm.cli", "snapshot", "--output-dir", "artifacts\online")
    if ($TargetDate) { $InitialArguments += @("--date", $TargetDate) }
    & $Python @InitialArguments
    if (-not $NoOpen) { Start-Process (Resolve-Path "artifacts\online\dashboard.html") }
    & $Python @Arguments
} else {
    & $Python @Arguments
    if (-not $NoOpen) { Start-Process (Resolve-Path "artifacts\online\dashboard.html") }
}
