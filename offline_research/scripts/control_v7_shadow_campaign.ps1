param(
    [ValidateSet('Register','Status','Verify')]
    [string]$Action = 'Status',
    [string]$ProjectRoot = '.'
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $ProjectRoot).Path
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Project Python is missing: $python"
}
$env:PYTHONPATH = "$root;$root\src"
$verb = $Action.ToLowerInvariant()
& $python -m v7_shadow.campaign $verb --project-root $root
if ($LASTEXITCODE -ne 0) {
    throw "V7 campaign controller failed with exit code $LASTEXITCODE"
}

