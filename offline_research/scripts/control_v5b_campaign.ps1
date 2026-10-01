param(
    [ValidateSet('Register','Start','Status','Review','Stop','Resume','Freeze')]
    [string]$Action = 'Status',
    [string]$ProjectRoot = '.',
    [string]$Packet = ''
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $ProjectRoot).Path
$python = Join-Path $root '.venv\Scripts\python.exe'
$env:PYTHONPATH = ((Join-Path $root 'src') + ';' + $root)
$mapped = $Action.ToLowerInvariant()
if ($Action -eq 'Start') { $mapped = 'epoch' }
$arguments = @('-m','v5b.campaign',$mapped,'--project-root',$root)
if ($Action -eq 'Review') {
    if (-not $Packet) { throw 'Review requires the actual agent review packet path' }
    $arguments += @('--packet',(Resolve-Path -LiteralPath $Packet).Path)
}
& $python @arguments
if ($LASTEXITCODE -ne 0) { throw "V5B $Action failed; inspect the error before resuming" }
