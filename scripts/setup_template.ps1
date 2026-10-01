param([switch]$SkipTests)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    $pythonCommand = Get-Command python -ErrorAction Stop
    & $pythonCommand.Source -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed.' }
}
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
& $pythonPath -m pip install -e '.[dev]'
if ($LASTEXITCODE -ne 0) { throw 'Package installation failed.' }
if (-not $SkipTests) {
    & $pythonPath -m pytest
    if ($LASTEXITCODE -ne 0) { throw 'Template verification failed.' }
}
Write-Host 'Template ready. Follow README steps 2 and 3 to run the example with actual research reviews.'
