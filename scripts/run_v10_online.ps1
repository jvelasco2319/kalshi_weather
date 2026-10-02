param(
    [ValidateSet('run','stage','capture','preview','settle','report','refresh')]
    [string]$Action = 'run',
    [string]$Date = '',
    [int]$WaitSeconds = 1200
)
$ErrorActionPreference = 'Stop'
$taskProject = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $taskProject '.venv\Scripts\python.exe'
$env:PYTHONPATH = (Join-Path $taskProject 'src') + ';' + $taskProject
$env:PYTHONDONTWRITEBYTECODE = '1'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Run .\scripts\setup_v10_dashboard.ps1 first.' }
& $taskPython (Join-Path $PSScriptRoot 'prepare_v10_dashboard.py')
if ($LASTEXITCODE -ne 0) { throw 'Frozen V10 verification failed.' }
& $taskPython -m v10_online --project-root $taskProject register
if ($LASTEXITCODE -ne 0) { throw 'Prospective V10 registration failed.' }
$taskArguments = @('-m','v10_online','--project-root',$taskProject,$Action)
if ($Action -notin @('report','refresh')) {
    if ([string]::IsNullOrWhiteSpace($Date)) { $Date = [DateTime]::UtcNow.ToString('yyyy-MM-dd') }
    $taskArguments += @('--date',$Date)
}
if ($Action -in @('run','capture')) { $taskArguments += @('--wait-seconds',[string]$WaitSeconds) }
& $taskPython @taskArguments
exit $LASTEXITCODE
