param(
    [ValidateSet('Register','Start','Status')]
    [string]$Action = 'Status',
    [string]$ProjectRoot = '.'
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $ProjectRoot).Path
$python = Join-Path $root '.venv\Scripts\python.exe'
$env:PYTHONPATH = ((Join-Path $root 'src') + ';' + $root)
& $python -m v6_2.campaign $Action.ToLowerInvariant() --project-root $root
if ($LASTEXITCODE -ne 0) { throw "V6.2 $Action failed." }

