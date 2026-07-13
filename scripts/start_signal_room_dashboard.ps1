param(
    [string]$Mode = "replay",
    [string]$SampleFixture = "tests/fixtures/signal_room_july7_replay.json",
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8765,
    [string]$SqlitePath = ""
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root
$env:PYTHONPATH = Join-Path $Root "src"

$argsList = @(
    "-m", "kalshi_weather.cli", "strategy-dashboard",
    "--mode", $Mode,
    "--host", $HostName,
    "--port", [string]$Port
)

if ($SampleFixture) {
    $argsList += @("--sample-fixture", $SampleFixture)
}

if ($SqlitePath) {
    $argsList += @("--sqlite-path", $SqlitePath)
}

python @argsList
