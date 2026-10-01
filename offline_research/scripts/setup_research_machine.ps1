[CmdletBinding()]
param(
    [ValidateSet('Auto', 'MirrorOnly', 'DownloadOnly', 'Status')]
    [string]$Mode = 'Auto',

    [string]$MirrorPath = '\\192.168.1.193\Kalshi\_weather\_llm',

    [int]$Workers = 8,
    [switch]$IncludeProbalytics,
    [string]$ProbalyticsUsername = $env:PROBALYTICS_USERNAME,
    [switch]$IncludeLocalModel,
    [switch]$SkipRuns,
    [switch]$SkipLocalRuntime
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$projectRoot = [System.IO.Path]::GetFullPath($projectRoot)
Set-Location -LiteralPath $projectRoot

if ($Workers -lt 1 -or $Workers -gt 16) {
    throw 'Workers must be between 1 and 16.'
}

$driveName = (Split-Path -Qualifier $projectRoot).TrimEnd(':')
$drive = Get-PSDrive -Name $driveName -ErrorAction SilentlyContinue
$recommendedFree = if ($IncludeLocalModel) { 105GB } else { 90GB }
if ($null -ne $drive -and $drive.Free -lt $recommendedFree) {
    Write-Warning ("The full archive may need {0:N0} GB free; this drive currently has {1:N1} GB." -f ($recommendedFree / 1GB), ($drive.Free / 1GB))
}

if ($Mode -in @('Auto', 'MirrorOnly')) {
    if (Test-Path -LiteralPath $MirrorPath -PathType Container) {
        $restoreArguments = @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            (Join-Path $projectRoot 'scripts\restore_research_data.ps1'),
            '-MirrorPath', $MirrorPath,
            '-ProjectRoot', $projectRoot
        )
        if ($SkipRuns) { $restoreArguments += '-SkipRuns' }
        if ($SkipLocalRuntime) { $restoreArguments += '-SkipLocalRuntime' }
        & powershell.exe @restoreArguments
        if ($LASTEXITCODE -ne 0) { throw 'Network restoration failed.' }
    }
    elseif ($Mode -eq 'MirrorOnly') {
        throw "The network mirror is unavailable: $MirrorPath"
    }
    else {
        Write-Host "Network mirror not found. Public-source acquisition will continue: $MirrorPath"
    }
}

$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    Write-Host 'Creating the Python 3.12 environment...'
    $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($null -ne $pyLauncher) {
        & $pyLauncher.Source -3.12 -m venv (Join-Path $projectRoot '.venv')
    }
    else {
        $python = Get-Command python.exe -ErrorAction Stop
        & $python.Source -m venv (Join-Path $projectRoot '.venv')
    }
    if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed. Install 64-bit Python 3.12 and rerun.' }
}

Write-Host 'Installing the pinned research dependencies...'
& $venvPython -m pip install --disable-pip-version-check -r (Join-Path $projectRoot 'requirements-local.lock')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $venvPython -m pip install --disable-pip-version-check --no-build-isolation --no-deps -e $projectRoot
if ($LASTEXITCODE -ne 0) { throw 'Editable project installation failed.' }

$env:PYTHONPATH = "$($projectRoot)\src;$projectRoot"
$bootstrapArguments = @(
    (Join-Path $projectRoot 'scripts\bootstrap_research_data.py'),
    $(if ($Mode -in @('Status', 'MirrorOnly')) { 'status' } else { 'run' }),
    '--project-root', $projectRoot,
    '--workers', "$Workers"
)
if ($IncludeProbalytics) {
    $bootstrapArguments += '--include-probalytics'
    if ($ProbalyticsUsername) {
        $bootstrapArguments += @('--probalytics-username', $ProbalyticsUsername)
    }
}
if ($IncludeLocalModel) {
    $bootstrapArguments += '--include-local-model'
}

& $venvPython @bootstrapArguments
if ($LASTEXITCODE -ne 0) { throw 'A selected acquisition stage failed. Inspect work\portable-bootstrap for its log.' }

Write-Host ''
Write-Host 'Setup finished. This command acquired data only; it did not start a campaign or place an order.'
