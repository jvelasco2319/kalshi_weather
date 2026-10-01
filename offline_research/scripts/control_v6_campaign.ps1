param(
    [ValidateSet('Register','Epoch','Status','Review','Stop','Resume','Freeze')]
    [string]$Action = 'Status',
    [string]$ProjectRoot = '.',
    [string]$Packet = ''
)
$ErrorActionPreference = 'Stop'
$v6Root = (Resolve-Path -LiteralPath $ProjectRoot).Path
$v6Python = Join-Path $v6Root '.venv\Scripts\python.exe'
$env:PYTHONPATH = ((Join-Path $v6Root 'src') + ';' + $v6Root)
$v6Arguments = @('-m','v6.campaign',$Action.ToLowerInvariant(),'--project-root',$v6Root)
if ($Action -eq 'Review') {
    if (-not $Packet) { throw 'Review requires a saved actual agent review packet.' }
    $v6Arguments += @('--packet',(Resolve-Path -LiteralPath $Packet).Path)
}
& $v6Python @v6Arguments
if ($LASTEXITCODE -ne 0) { throw "V6 $Action failed. Inspect the error before resuming." }
