param(
    [ValidateSet('Start','Status','Resume')]
    [string]$Action = 'Status',
    [string]$ProjectRoot = '.'
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $ProjectRoot).Path
$python = Join-Path $root '.venv\Scripts\python.exe'
$env:PYTHONPATH = ((Join-Path $root 'src') + ';' + $root)
$pidPath = Join-Path $root 'runs\v5a_campaign.pid'
$stdoutPath = Join-Path $root 'runs\v5a_campaign.stdout.log'
$stderrPath = Join-Path $root 'runs\v5a_campaign.stderr.log'

function Get-BoundProcess {
    if (-not (Test-Path -LiteralPath $pidPath)) { return $null }
    $processId = [int](Get-Content -Raw -LiteralPath $pidPath)
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $null }
    if ($process.CommandLine -notlike '*v5a.development_search*') {
        throw 'PID file does not identify the V5A campaign process'
    }
    return $process
}

function Show-Status {
    & $python -m v5a.development_search status --project-root $root
    if ($LASTEXITCODE -ne 0) { throw 'V5A campaign status failed' }
}

if ($Action -eq 'Status') { Show-Status; exit 0 }
$existing = Get-BoundProcess
if ($null -ne $existing) { Show-Status; exit 0 }

& $python -m v5a.development_search register --project-root $root | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'V5A campaign registration failed' }
$process = Start-Process -FilePath $python -ArgumentList @(
    '-m', 'v5a.development_search', 'run', '--project-root', $root
) -WorkingDirectory $root -WindowStyle Hidden `
  -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
Set-Content -LiteralPath $pidPath -Value ([string]$process.Id) -Encoding ascii
Start-Sleep -Milliseconds 750
if ($process.HasExited) {
    throw "V5A campaign exited immediately with code $($process.ExitCode); inspect $stderrPath"
}
Show-Status
