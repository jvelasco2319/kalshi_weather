param(
    [ValidateSet('Start','Status','Resume')]
    [string]$Action = 'Status',
    [string]$ProjectRoot = '.'
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $ProjectRoot).Path
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw "Project Python missing: $python" }
$env:PYTHONPATH = (Join-Path $root 'src')
$runId = 'v5p-historical-20250701-20260831'
$runDir = Join-Path $root ("runs\v5p_acquisition\" + $runId)
$statePath = Join-Path $runDir 'recovery-state.json'
$pidPath = Join-Path $runDir 'process.pid'
$stdoutPath = Join-Path $root 'runs\v5p_acquisition.stdout.log'
$stderrPath = Join-Path $root 'runs\v5p_acquisition.stderr.log'

function Get-BoundProcess {
    if (-not (Test-Path -LiteralPath $pidPath)) { return $null }
    $processId = [int](Get-Content -Raw -LiteralPath $pidPath)
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $null }
    if ($process.CommandLine -notlike '*v5.acquire_probability_evidence*' -or
        $process.CommandLine -notlike ("*" + $runId + "*")) {
        throw "PID file does not identify the registered V5P acquisition process"
    }
    return $process
}

function Show-Status {
    & $python -m v5p.campaign status --project-root $root
    if ($LASTEXITCODE -ne 0) { throw 'V5P status failed' }
}

if ($Action -eq 'Status') { Show-Status; exit 0 }

& $python -m v5p.campaign register --project-root $root | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'V5P registration failed' }

$existing = Get-BoundProcess
if ($null -ne $existing) { Show-Status; exit 0 }

if (Test-Path -LiteralPath $statePath) {
    $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
    if ($state.status -eq 'COMPLETE') { Show-Status; exit 0 }
}

New-Item -ItemType Directory -Force -Path $runDir | Out-Null
$arguments = @(
    '-m', 'v5.acquire_probability_evidence', 'run',
    '--project-root', $root, '--run-id', $runId
)
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $root `
    -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
Set-Content -LiteralPath $pidPath -Value ([string]$process.Id) -Encoding ascii
Start-Sleep -Milliseconds 750
if ($process.HasExited) {
    throw "V5P acquisition exited immediately with code $($process.ExitCode); inspect $stderrPath"
}
Show-Status
