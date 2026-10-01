param(
    [ValidateSet('Start','Status','Resume')]
    [string]$Action = 'Status',
    [string]$ProjectRoot = '.'
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $ProjectRoot).Path
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw "Project Python missing: $python" }
$env:PYTHONPATH = ((Join-Path $root 'src') + ';' + $root)
$runDir = Join-Path $root 'runs\v5b_untouched_acquisition'
$statePath = Join-Path $runDir 'weather-recovery-state.json'
$pidPath = Join-Path $runDir 'weather-process.pid'
$stdoutPath = Join-Path $runDir 'weather.stdout.log'
$stderrPath = Join-Path $runDir 'weather.stderr.log'

function Get-BoundProcess {
    if (-not (Test-Path -LiteralPath $pidPath)) { return $null }
    $processId = [int](Get-Content -Raw -LiteralPath $pidPath)
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $null }
    if ($process.CommandLine -notlike '*v5b_confirmation.weather_acquisition*') {
        throw 'PID file does not identify the registered untouched-weather process'
    }
    return $process
}

function Show-Status {
    & $python -m v5b_confirmation.weather_acquisition status --project-root $root
    if ($LASTEXITCODE -ne 0) { throw 'Untouched-weather status failed' }
}

if ($Action -eq 'Status') { Show-Status; exit 0 }
$existing = Get-BoundProcess
if ($null -ne $existing) { Show-Status; exit 0 }
if (Test-Path -LiteralPath $statePath) {
    $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
    if ($state.status -eq 'COMPLETE') { Show-Status; exit 0 }
}

New-Item -ItemType Directory -Force -Path $runDir | Out-Null
$arguments = @('-m','v5b_confirmation.weather_acquisition','run','--project-root',$root)
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $root `
    -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
Set-Content -LiteralPath $pidPath -Value ([string]$process.Id) -Encoding ascii
Start-Sleep -Milliseconds 750
if ($process.HasExited) {
    throw "Untouched-weather acquisition exited immediately with code $($process.ExitCode); inspect $stderrPath"
}
Show-Status
