param([Parameter(ValueFromRemainingArguments = $true)][string[]]$LauncherArguments)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$portablePython = Join-Path $repoRoot '.venv/Scripts/python.exe'
$portableLauncher = Join-Path $PSScriptRoot 'launch.py'
if (-not (Test-Path -LiteralPath $portablePython -PathType Leaf)) {
    throw 'Run final_testing/setup.ps1 before starting the dashboard.'
}
if (-not (Test-Path -LiteralPath $portableLauncher -PathType Leaf)) {
    throw 'The portable dashboard launcher is missing.'
}
& $portablePython -B $portableLauncher @LauncherArguments
if ($LASTEXITCODE -ne 0) { throw 'The portable dashboard stopped. Check its message above.' }
