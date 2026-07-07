$ErrorActionPreference = "Continue"

$Root = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
Set-Location -LiteralPath $Root

Write-Output "Direct NOAA / Herbie optional dependency installer"
Write-Output "Project root: $Root"
Write-Output ""

$commands = @(
    @{ Name = "install"; Command = "python -m pip install --upgrade herbie-data xarray cfgrib eccodes" },
    @{ Name = "verify_herbie"; Command = "python -c `"import sys; sys.stdout.reconfigure(encoding='utf-8'); from herbie import Herbie; print('Herbie OK')`"" },
    @{ Name = "verify_xarray"; Command = "python -c `"import xarray; print('xarray OK')`"" },
    @{ Name = "verify_cfgrib"; Command = "python -c `"import cfgrib; print('cfgrib OK')`"" },
    @{ Name = "verify_eccodes"; Command = "python -c `"import eccodes; print('eccodes OK')`"" }
)

$failed = @()
foreach ($item in $commands) {
    Write-Output ""
    Write-Output "== $($item.Name) =="
    Invoke-Expression $item.Command
    if ($LASTEXITCODE -ne 0) {
        $failed += $item.Name
        Write-Output "FAILED: $($item.Name) exit=$LASTEXITCODE"
    }
}

Write-Output ""
if ($failed.Count -eq 0) {
    Write-Output "Direct NOAA dependencies verified."
    exit 0
}

Write-Output "Direct NOAA dependency setup is incomplete."
Write-Output "Failed steps: $($failed -join ', ')"
Write-Output ""
Write-Output "Next steps on Windows:"
Write-Output "- Try installing ecCodes through conda/mamba if available."
Write-Output "  Example: conda install -c conda-forge eccodes cfgrib"
Write-Output "- Or keep direct NOAA models marked unavailable."
Write-Output "- Open-Meteo/current estimates continue working without these optional dependencies."
exit 1
