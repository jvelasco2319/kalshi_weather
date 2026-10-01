param(
    [ValidateSet('Register','Start','Status')]
    [string]$Action = 'Status',
    [string]$ProjectRoot = '.'
)
$ErrorActionPreference = 'Stop'
$campaignRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$campaignPython = Join-Path $campaignRoot '.venv\Scripts\python.exe'
$env:PYTHONPATH = ((Join-Path $campaignRoot 'src') + ';' + $campaignRoot)
& $campaignPython -m v6_1.campaign $Action.ToLowerInvariant() --project-root $campaignRoot
if ($LASTEXITCODE -ne 0) { throw "V6.1 $Action failed. Inspect the error before continuing." }

