param(
    [string]$Python = '',
    [string]$Wheelhouse = '',
    [switch]$Plan,
    [switch]$SkipInstall
)
$ErrorActionPreference = 'Stop'
$setupScript = Join-Path $PSScriptRoot 'setup.py'
$setupArguments = @('-B', $setupScript)
if ($Plan) { $setupArguments += '--plan' }
if ($SkipInstall) { $setupArguments += '--skip-install' }
if ($Wheelhouse) { $setupArguments += @('--wheelhouse', $Wheelhouse) }
if ($Python) {
    & $Python @setupArguments
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3.12 @setupArguments
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python @setupArguments
} else {
    throw 'Install 64-bit Windows Python 3.12, then run this setup script again.'
}
if ($LASTEXITCODE -ne 0) { throw 'Portable setup did not finish. Read the setup message above.' }
