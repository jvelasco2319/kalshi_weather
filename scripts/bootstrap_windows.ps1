[CmdletBinding()]
param(
    [string]$Python = "python",
    [switch]$WithoutDevTools
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$VenvRoot = Join-Path $Root ".venv"
$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"

Set-Location -LiteralPath $Root

if (-not (Test-Path -LiteralPath $VenvPython)) {
    & $Python -c "import sys; assert sys.version_info >= (3, 11), 'Python 3.11 or newer is required'"
    if ($LASTEXITCODE -ne 0) { throw "Python 3.11 or newer is required." }
    & $Python -m venv $VenvRoot
    if ($LASTEXITCODE -ne 0) { throw "Could not create $VenvRoot" }
}

& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Could not upgrade pip." }

$InstallTarget = if ($WithoutDevTools) { ".[full]" } else { ".[dev,full]" }
& $VenvPython -m pip install -e $InstallTarget
if ($LASTEXITCODE -ne 0) { throw "Could not install kalshi-weather dependencies." }

if (-not (Test-Path -LiteralPath (Join-Path $Root ".env"))) {
    Copy-Item -LiteralPath (Join-Path $Root ".env.example") -Destination (Join-Path $Root ".env")
}

& $VenvPython -m kalshi_weather.cli init-runtime --root $Root
if ($LASTEXITCODE -ne 0) { throw "Could not initialize runtime directories." }

& $VenvPython -m kalshi_weather.cli --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw "CLI startup check failed." }

& $VenvPython -c "import cfgrib, eccodes, fastapi, herbie, jinja2, uvicorn, xarray; from kalshi_weather.signal_room.app import create_app; create_app(mode='replay')"
if ($LASTEXITCODE -ne 0) { throw "Herbie or dashboard dependency check failed." }

Write-Host ""
Write-Host "kalshi-weather is ready in $Root"
Write-Host "Activate it with: .\.venv\Scripts\Activate.ps1"
Write-Host "Update .env with a descriptive NWS_USER_AGENT before live collection."
