param(
    [ValidateSet('Register','Epoch','Status','Review','Stop','Resume','Freeze')]
    [string]$Action = 'Status',
    [string]$ProjectRoot = '.',
    [string]$Packet
)
$ErrorActionPreference = 'Stop'
$campaignRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$campaignPython = Join-Path $campaignRoot '.venv\Scripts\python.exe'
$campaignArguments = @('-m', 'v5b_next.campaign', $Action.ToLowerInvariant(), '--project-root', $campaignRoot)
if ($Action -eq 'Review') {
    if (-not $Packet) { throw 'Review requires a packet path.' }
    $campaignArguments += @('--packet', (Resolve-Path -LiteralPath $Packet).Path)
}
Push-Location -LiteralPath $campaignRoot
try {
    & $campaignPython @campaignArguments
    if ($LASTEXITCODE -ne 0) { throw "Successor controller exited with code $LASTEXITCODE" }
} finally {
    Pop-Location
}
