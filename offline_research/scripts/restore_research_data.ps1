[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$MirrorPath,

    [string]$ProjectRoot = (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)),

    [switch]$SkipRuns,
    [switch]$SkipLocalRuntime
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$sourceRoot = [System.IO.Path]::GetFullPath($MirrorPath)
$destinationRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
if (-not (Test-Path -LiteralPath $sourceRoot -PathType Container)) {
    throw "Data mirror is unavailable: $MirrorPath"
}
if (-not (Test-Path -LiteralPath (Join-Path $destinationRoot 'pyproject.toml') -PathType Leaf)) {
    throw "Destination is not the offline research project: $destinationRoot"
}

function Copy-ResearchTree {
    param([string]$RelativePath)
    $source = Join-Path $sourceRoot $RelativePath
    if (-not (Test-Path -LiteralPath $source -PathType Container)) {
        Write-Host "Mirror does not contain $RelativePath; continuing."
        return
    }
    $destination = Join-Path $destinationRoot $RelativePath
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    Write-Host "Restoring $RelativePath from the network mirror..."
    & robocopy $source $destination /E /Z /J /R:3 /W:5 /MT:8 /COPY:DAT /DCOPY:DAT /XJ /XF '*.pem' '*.key' '*.p12' '*.pfx' '.env' '.env.*'
    $code = $LASTEXITCODE
    if ($code -gt 7) {
        throw "Robocopy failed for $RelativePath with exit code $code"
    }
}

Copy-ResearchTree 'data'
if (-not $SkipRuns) {
    Copy-ResearchTree 'runs'
}
if (-not $SkipLocalRuntime) {
    Copy-ResearchTree 'external\llama_cpp'
}

Write-Host 'Network restoration finished. Existing matching files were skipped; missing or changed files were copied.'
